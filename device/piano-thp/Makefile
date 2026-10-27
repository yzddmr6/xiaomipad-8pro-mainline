# SPDX-License-Identifier: MIT
CXX ?= g++
OUT ?= build
CPPFLAGS += -Isrc $(if $(JSON_INCLUDE),-I$(JSON_INCLUDE))
CXXFLAGS ?= -O2 -std=c++20
CXXFLAGS += -Wall -Wextra -Werror -MMD -MP
# Header paths end up in assertion strings; keep the build host's JSON_INCLUDE location out of the binary.
CXXFLAGS += $(if $(JSON_INCLUDE),-ffile-prefix-map=$(JSON_INCLUDE)=/usr/include)
CORE := nvt_touch_core nvt_finger_filter frame processor
STYLUS := nvt_stylus nvt_stylus_frame nvt_stylus_core pen
SOURCES := $(CORE) $(STYLUS) calibration panel replay stream live_pipeline input live
OBJECTS := $(addprefix $(OUT)/,$(addsuffix .o,$(SOURCES)))
CORE_OBJECTS := $(addprefix $(OUT)/,$(addsuffix .o,$(CORE)))

.PHONY: all test
all: $(OUT)/piano-thp-replay $(OUT)/piano-thp-live

$(OUT):
	mkdir -p "$@"

$(OUT)/%.o: src/%.cpp | $(OUT)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -c $< -o $@

$(OUT)/piano-thp-replay: $(CORE_OBJECTS) $(addprefix $(OUT)/,$(addsuffix .o,$(STYLUS))) $(OUT)/calibration.o $(OUT)/stream.o $(OUT)/replay.o
	$(CXX) $(LDFLAGS) $^ $(LDLIBS) -o $@

$(OUT)/piano-thp-live: $(CORE_OBJECTS) $(OUT)/calibration.o $(OUT)/panel.o $(OUT)/stream.o $(OUT)/live_pipeline.o $(OUT)/input.o $(OUT)/live.o
	$(CXX) $(LDFLAGS) $^ $(LDLIBS) -o $@

$(OUT)/test-calibration: tests/calibration.cpp $(CORE_OBJECTS) $(OUT)/calibration.o
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) $(LDFLAGS) $^ $(LDLIBS) -o $@

$(OUT)/test-live: tests/live.cpp $(CORE_OBJECTS) $(OUT)/calibration.o $(OUT)/panel.o $(OUT)/stream.o $(OUT)/live_pipeline.o
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) $(LDFLAGS) $^ $(LDLIBS) -o $@

$(OUT)/test-uinput: tests/uinput.cpp $(OUT)/input.o
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) $(LDFLAGS) $^ $(LDLIBS) -o $@

$(OUT)/test-stylus: tests/stylus.cpp $(CORE_OBJECTS) $(addprefix $(OUT)/,$(addsuffix .o,$(STYLUS)))
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) $(LDFLAGS) $^ $(LDLIBS) -o $@

test: $(OUT)/test-calibration $(OUT)/test-live $(OUT)/test-stylus
	$(RUNNER) $(OUT)/test-calibration
	$(RUNNER) $(OUT)/test-live
	$(RUNNER) $(OUT)/test-stylus

-include $(OBJECTS:.o=.d)
