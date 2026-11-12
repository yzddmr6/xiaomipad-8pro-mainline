// SPDX-License-Identifier: MIT
// Two contexts, one public llama.cpp state transfer. No scheduler patches.
#include "llama.h"
#include "ggml-backend.h"
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iterator>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>
static bool diagnostic = false, dsp_profile = false;
static void log_message(enum ggml_log_level level, const char *text, void *) {
    if (dsp_profile || level == GGML_LOG_LEVEL_WARN || level == GGML_LOG_LEVEL_ERROR) std::fputs(text, stderr);
}
using Clock = std::chrono::steady_clock;
static double seconds(Clock::time_point a) { return std::chrono::duration<double>(Clock::now()-a).count(); }
using Model = std::unique_ptr<llama_model, decltype(&llama_model_free)>;
using Context = std::unique_ptr<llama_context, decltype(&llama_free)>;
static void eval(llama_context *ctx, const llama_token *tokens, int n, int pos) {
    auto *batch = llama_batch_ext_init(ctx);
    if (!batch) throw std::runtime_error("batch allocation failed");
    for (int i=0; i<n; ++i) {
        int idx = llama_batch_ext_add_token(batch, 0, tokens[i]);
        llama_pos p = pos+i;
        llama_batch_ext_set_pos(batch, idx, &p);
    }
    llama_batch_ext_set_output_logits(batch, n-1, true);
    int rc = llama_process(ctx, LLAMA_PROCESS_TYPE_DECODE, batch);
    llama_batch_ext_free(batch);
    if (rc) throw std::runtime_error("inference failed: " + std::to_string(rc));
}
int main(int argc, char **argv) try {
    if (argc != 6) {
        std::fprintf(stderr,"Usage: npu-hybrid MODEL PROMPT_FILE auto|hybrid|cpu|npu TOKENS LIBDIR\n");
        return 2;
    }
    std::string mode=argv[3], lib=argv[5];
    const bool automatic = mode=="auto";
    if (!automatic && mode!="hybrid" && mode!="cpu" && mode!="npu") throw std::runtime_error("invalid backend");
    int generate=std::stoi(argv[4]);
    if (generate<1 || generate>512) throw std::runtime_error("token limit must be 1..512");
    std::ifstream input(argv[2]);
    if (!input) throw std::runtime_error("cannot read prompt");
    std::string prompt((std::istreambuf_iterator<char>(input)),{});
    if (prompt.empty() || prompt.size()>65536) throw std::runtime_error("prompt must be 1..65536 bytes");
    diagnostic = std::getenv("NPU_DEMO_DIAGNOSTICS") != nullptr;
    const char *profile = std::getenv("GGML_HEXAGON_PROFILE");
    dsp_profile = profile && std::string(profile) != "0";
    llama_log_set(log_message,nullptr);
    ggml_log_set(log_message,nullptr);
    // Explicit loading keeps the CPU control run free of FastRPC sessions.
    if (!ggml_backend_load((lib+"/libggml-cpu.so").c_str())) throw std::runtime_error("CPU backend unavailable");
    if (!automatic && mode!="cpu" && !ggml_backend_load((lib+"/libggml-hexagon.so").c_str())) throw std::runtime_error("NPU backend unavailable");
    llama_backend_init();
    ggml_backend_dev_t cpu_devices[] = {nullptr};
    ggml_backend_dev_t npu_devices[] = {ggml_backend_dev_by_name("HTP0"),nullptr};
    if (!automatic && mode!="cpu" && !npu_devices[0]) throw std::runtime_error("HTP0 unavailable");
    auto load=[&](bool npu) {
        auto p=llama_model_default_params();
        p.n_gpu_layers=npu?99:0; p.devices=npu?npu_devices:cpu_devices;
        Model m(llama_model_load_from_file(argv[1],p),llama_model_free);
        if (!m) throw std::runtime_error("model load failed");
        return m;
    };
    const auto load_start=Clock::now();
    Model cpu(nullptr,llama_model_free), npu(nullptr,llama_model_free);
    if(mode!="npu") cpu=load(false);
    if(!automatic && mode!="cpu") npu=load(true);
    const auto *vocab=llama_model_get_vocab(cpu?cpu.get():npu.get());
    int count=-llama_tokenize(vocab,prompt.data(),prompt.size(),nullptr,0,true,true);
    if (count<1 || count+generate>4096) throw std::runtime_error("demo context limit is 4096 tokens");
    if (automatic) {
        // Single-shot CLI pays a second model load. Keep short prompts on CPU.
        // 512 is a conservative policy threshold, not a universal optimum.
        mode = count-1 >= 512 ? "hybrid" : "cpu";
        if (mode=="hybrid") {
            if (!ggml_backend_load((lib+"/libggml-hexagon.so").c_str())) throw std::runtime_error("NPU backend unavailable");
            npu_devices[0]=ggml_backend_dev_by_name("HTP0");
            if (!npu_devices[0]) throw std::runtime_error("HTP0 unavailable");
            npu=load(true);
        }
    }
    std::vector<llama_token> tokens(count);
    if(llama_tokenize(vocab,prompt.data(),prompt.size(),tokens.data(),count,true,true)!=count) throw std::runtime_error("tokenization failed");
    auto context=[&](llama_model *m, bool offload) {
        auto p=llama_context_default_params();
        p.n_ctx=4096; p.n_batch=256; p.n_ubatch=256;
        p.n_threads=4; p.n_threads_batch=4;
        p.flash_attn_type=LLAMA_FLASH_ATTN_TYPE_ENABLED;
        p.offload_kqv=offload; p.op_offload=offload; p.no_perf=false;
        Context c(llama_init_from_model(m,p),llama_free);
        if (!c) throw std::runtime_error("context creation failed");
        return c;
    };
    Context cc(nullptr,llama_free), nc(nullptr,llama_free);
    if(cpu) cc=context(cpu.get(),false);
    if(npu) nc=context(npu.get(),true);
    double load_s=seconds(load_start);
    const auto total_start=Clock::now();
    auto t=Clock::now();
    llama_context *prefill=mode=="cpu"?cc.get():nc.get();
    if(diagnostic) std::fprintf(stderr,"NPU_DEMO phase=prefill backend=%s tokens=%d\n",mode=="cpu"?"CPU":"HTP0",count-1);
    // Leave the last prompt token for decode: state API does not copy logits.
    for(int pos=0;pos<count-1;) { int n=std::min(256,count-1-pos);eval(prefill,tokens.data()+pos,n,pos);pos+=n; }
    double prefill_s=seconds(t), transfer_s=0;size_t state_bytes=0;
    if(mode=="hybrid") {
        t=Clock::now();
        state_bytes=llama_state_get_size(nc.get());
        std::vector<uint8_t> state(state_bytes);
        if(llama_state_get_data(nc.get(),state.data(),state.size())!=state.size() ||
           llama_state_set_data(cc.get(),state.data(),state.size())!=state.size()) throw std::runtime_error("KV state transfer failed");
        transfer_s=seconds(t);
        if(diagnostic) std::fprintf(stderr,"NPU_DEMO phase=transfer bytes=%zu seconds=%.6f\n",state_bytes,transfer_s);
    }
    llama_context *decode=mode=="npu"?nc.get():cc.get();
    if(diagnostic) std::fprintf(stderr,"NPU_DEMO phase=decode backend=%s\n",mode=="npu"?"HTP0":"CPU");
    t=Clock::now();
    eval(decode,&tokens.back(),1,count-1);
    double last_prompt_s=seconds(t);
    auto *sampler=llama_sampler_init_greedy();
    int emitted=0, evals=0;
    t=Clock::now();
    for(int i=0;i<generate;++i) {
        llama_token next=llama_sampler_sample(sampler,decode,-1);
        if(llama_vocab_is_eog(vocab,next))break;
        char text[512];int n=llama_token_to_piece(vocab,next,text,sizeof(text),0,true);
        if(n<0) throw std::runtime_error("token piece exceeds demo buffer");
        std::fwrite(text,1,n,stdout); std::fflush(stdout); ++emitted;
        if(i+1<generate) {eval(decode,&next,1,count+i);++evals;}
    }
    double decode_s=seconds(t), total_s=seconds(total_start);
    llama_sampler_free(sampler);
    std::printf("\n");
    if(diagnostic) std::fprintf(stderr,"NPU_DEMO_RESULT {\"mode\":\"%s\",\"prompt_tokens\":%d,\"output_tokens\":%d,\"decode_evals\":%d,\"load_s\":%.6f,\"prefill_s\":%.6f,\"transfer_s\":%.6f,\"last_prompt_s\":%.6f,\"decode_s\":%.6f,\"inference_s\":%.6f,\"state_bytes\":%zu}\n",mode.c_str(),count,emitted,evals,load_s,prefill_s,transfer_s,last_prompt_s,decode_s,total_s,state_bytes);
    if (!diagnostic) std::fprintf(stderr,"Prompt: %s %.3f s; transfer %.3f s; generation: %s %.3f s (%d tokens).\n",mode=="cpu"?"CPU":"NPU",prefill_s+last_prompt_s,transfer_s,mode=="npu"?"NPU":"CPU",decode_s,emitted);
    return 0;
} catch(const std::exception &e) {std::fprintf(stderr,"npu-demo: %s\n",e.what());return 1;}
