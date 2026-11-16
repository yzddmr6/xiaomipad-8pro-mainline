#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""Run only inside the existing disposable apt-scenarios chroot.

Use the real attachment installer with compact APT batches to inject package
configuration failures and a release gap, without changing release artefacts.
"""
from pathlib import Path
import functools, hashlib, http.server, importlib.util, json, os, shutil, subprocess, sys, tarfile, threading, zipfile
assert Path('/etc/release-disposable-test-root').is_file()
os.environ['PATH']='/usr/local/sbin:'+os.environ['PATH']
sys.path.insert(0,'/usr/lib/mainline-update')
from core import Updater, UpdateError
installer=Path(sys.argv[1])
base=Path('/opt/update-fixtures');base.mkdir(exist_ok=True)
def run(args):subprocess.run(list(map(str,args)),check=True,stdout=subprocess.DEVNULL)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
for product in ((sys.argv[3],) if len(sys.argv)>3 else ('piano','erhai')):
    store=Path('/var/lib')/product/'releases'
    # Overlay lower directories cannot be renamed across layers. This store
    # belongs to the disposable fixture and is reset for compact transactions.
    if store.exists():shutil.rmtree(store)
    boot=Path('/var/lib')/product/'switch/ubuntu';boot.mkdir(parents=True,exist_ok=True)
    for name in ('boot.img','init_boot.img','vendor_boot.img'):(boot/name).write_bytes(b'offline boot fixture')
    Path('/etc/offline-product').write_text(product)
    Path('/results').mkdir(exist_ok=True)
    slots=Path('/dev/disk/by-partlabel')
    slots.mkdir(parents=True,exist_ok=True)
    names=('boot','init_boot','vendor_boot')
    for name in names:
        node=slots/(name+'_a')
        assert not node.is_symlink() and not node.is_block_device()
        node.write_bytes(b'old '+name.encode())
    for tool in ('piano-boot-switch','erhai-switch','erhai-switch-install-ubuntu-images'):
        stub=Path('/usr/local/sbin')/tool
        shutil.copyfile(Path(__file__).with_name('boot-switch-fixture.py'),stub)
        stub.chmod(0o755)
    fixtures=base/product;fixtures.mkdir(exist_ok=True)
    batches={}
    for version in ('70.0','71.0','72.0'):
        pkg=fixtures/('pkg-'+version);(pkg/'DEBIAN').mkdir(parents=True)
        (pkg/'DEBIAN/control').write_text(f'Package: piano-update-fixture\nVersion: {version}\nArchitecture: all\nMaintainer: Offline fixture <noreply@example.invalid>\nDescription: Offline updater transaction fixture\n')
        hook=pkg/'DEBIAN/postinst'
        hook.write_text('#!/bin/sh\nset -e\n'+('if [ -f /opt/update-fixture/fail ]; then\n mode=$(cat /opt/update-fixture/fail)\n [ "$mode" != once ] || rm /opt/update-fixture/fail\n exit 1\nfi\n' if version=='72.0' else '')+f'mkdir -p /opt/update-fixture\necho {version} > /opt/update-fixture/version\n')
        with hook.open('a') as script:
            for n in names:
                script.write(f'printf "%s" "{version}-{n}" > "{boot}/{n}.img"\n')
        hook.chmod(0o755)
        deb=fixtures/(version+'.deb');run(['dpkg-deb','--build',pkg,deb])
        batch=fixtures/('repositories/'+product+'-'+version)
        (batch/'pool').mkdir(parents=True)
        shutil.copyfile(deb,batch/'pool'/deb.name);shutil.copyfile(installer,batch/'install.py');(batch/'install.py').chmod(0o755)
        spec=importlib.util.spec_from_file_location('release_installer',installer);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        module.inventory(batch,batch.name,product,['piano-update-fixture']);module.verify(batch)
        batches[version]=batch
    def bundle(path,items):
        if product=='piano':
            with zipfile.ZipFile(path,'w') as z:
                for batch,name in items:
                    for f in batch.rglob('*'):
                        if f.is_file():z.write(f,'repositories/'+name+'/'+str(f.relative_to(batch)))
        else:
            with tarfile.open(path,'w') as t:
                for batch,name in items:t.add(batch,arcname='repositories/'+name)
    suffix='.zip' if product=='piano' else '.tar'
    pattern='fixture-{version}'+suffix
    for version in ('70.0','71.0','72.0'):
        release=fixtures/version;release.mkdir()
        package=release/pattern.format(version=version)
        bundle(package,[(batches[version],product+'-'+version)])
        entries=[package]
        if version=='72.0':
            rollback=release/('rollback'+suffix)
            # The release immediately before 72.0 is 71.0; current 70.0 must fetch its own page.
            old=batches['71.0']; renamed=fixtures/'rollback-batch'
            shutil.copytree(old,renamed)
            data=json.loads((renamed/'manifest.json').read_text());data['id']=product+'-71.0-rollback-72.0';(renamed/'manifest.json').write_text(json.dumps(data))
            (renamed/'SHA256SUMS').write_text(''.join(sha(f)+'  '+str(f.relative_to(renamed))+'\n' for f in sorted(renamed.rglob('*')) if f.is_file() and f.name!='SHA256SUMS'))
            if product=='piano':bundle(rollback,[(renamed,data['id'])]);entries.append(rollback)
            else:bundle(package,[(batches[version],product+'-'+version),(renamed,data['id'])])
        (release/'SHA256SUMS.assets').write_text(''.join(sha(f)+'  '+f.name+'\n' for f in entries))
    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self,*a):pass
        def do_GET(self):
            if '/releases/tags/' in self.path:
                tag=self.path.rsplit('/',1)[-1];v=tag.removeprefix('v');directory=fixtures/v
                data=json.dumps(dict(tag_name=tag,assets=[dict(name=p.name,size=p.stat().st_size,browser_download_url=repository+'/releases/download/'+tag+'/'+p.name) for p in directory.iterdir()])).encode()
                self.send_response(200);self.end_headers();self.wfile.write(data);return
            parts=self.path.split('/');self.path='/'+parts[-2].removeprefix('v')+'/'+parts[-1]
            super().do_GET()
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Handler,directory=str(fixtures)))
    repository=f'http://127.0.0.1:{server.server_port}/fixture/project'
    threading.Thread(target=server.serve_forever,daemon=True).start()
    config=dict(display_name=product,repository=repository,repo_slug='fixture/project',api_base=f'http://127.0.0.1:{server.server_port}',product=product,installed_version_file='/opt/update-fixture/version',checksum_asset='SHA256SUMS.assets',package_asset=pattern,archive_format='zip' if product=='piano' else 'tar',new_batch=product+'-{version}',previous_batch=product+'-{current}-rollback-{version}')
    if product=='piano':config['rollback_asset']='rollback.zip'
    configfile=fixtures/'project.json';configfile.write_text(json.dumps(config))
    for scenario in ('normal','recovery-once','failure-rollback','jump'):
        if len(sys.argv)>2 and sys.argv[2] != 'all' and scenario != sys.argv[2]:
            continue
        if store.exists():shutil.rmtree(store)
        cache=fixtures/('cache-'+scenario);state=fixtures/('state-'+scenario)
        os.environ.update(MAINLINE_UPDATE_CACHE=str(cache),MAINLINE_UPDATE_STATE=str(state),MAINLINE_UPDATE_TEST_NO_INHIBIT='1')
        old='70.0' if scenario=='jump' else '71.0'
        fail=Path('/opt/update-fixture/fail');fail.unlink(missing_ok=True)
        run(['dpkg','-i',fixtures/(old+'.deb')])
        # The currently retained batch must become removable only in the new transaction.
        stale=store/(product+'-obsolete')
        stale.mkdir(parents=True)
        shutil.copyfile(batches[old]/'manifest.json',stale/'manifest.json')
        for n in names:
            (boot/(n+'.img')).write_bytes(b'old '+n.encode())
            (slots/(n+'_a')).write_bytes(b'old '+n.encode())
        u=Updater(configfile)
        prepared=u.prepare('72.0')
        assert Path(prepared['previous_batch']).name==(product+'-70.0' if scenario=='jump' else product+'-71.0-rollback-72.0')
        if scenario.startswith('recovery') or scenario=='failure-rollback':fail.write_text('once' if scenario=='recovery-once' else 'always')
        preview=prepared['cleanup_preview']
        assert preview['transaction']['to']==product+'-72.0' and str(stale) in {x.get('path') for x in preview['remove']}
        assert stale.exists() and not (store/'transaction.json').exists()
        calls=[];actual=u._run
        def traced(argv,**kw):calls.append(list(map(str,argv)));return actual(argv,**kw)
        u._run=traced
        result=u.install()
        assert result['phase']==('failed_rollback' if scenario=='failure-rollback' else 'reboot_pending'),result
        if 'recovery' in scenario or scenario=='failure-rollback':
            assert ['/usr/bin/dpkg','--configure','-a'] in calls,calls
        if scenario=='recovery-once':
            assert ['/usr/bin/apt-get','-y','-f','install'] in calls and sum('upgrade' in c for c in calls)==2,calls
        if result['phase']=='reboot_pending':
            assert result['freed_bytes']==preview['freed_bytes']
            assert all((slots/(n+'_a')).read_bytes()==('72.0-'+n).encode() for n in names)
        if scenario=='normal':
            fault=Path('/run/fixture-omit-slot-write')
            fault.touch()
            try:
                bad=u.rollback()
                assert bad['phase']=='failed_rollback' and 'readback differs' in bad['rollback_error'],bad
                assert json.loads((store/'transaction.json').read_text())['phase']!='rolled-back'
            finally:
                fault.unlink()
        # Keep the failed new package's fault active: the previous package's
        # postinst works, so whole-release rollback must actually downgrade it.
        if scenario != 'failure-rollback':
            fail.unlink(missing_ok=True)
        result=u.rollback();assert result['phase']=='rolled_back',result
        assert result['freed_bytes']==result['installer_result']['freed_bytes']
        assert Path('/opt/update-fixture/version').read_text().strip()==old
        assert all((slots/(n+'_a')).read_bytes()==b'old '+n.encode() for n in names)
        if scenario=='normal':
            prepared=u.prepare('72.0')
            assert u.install()['phase']=='reboot_pending'
            assert all((slots/(n+'_a')).read_bytes()==('72.0-'+n).encode() for n in names)
        assert not subprocess.check_output(['dpkg','--audit'],text=True).strip()
        print('PASS',product,scenario,'real attachment installer upgrade/rollback',flush=True)
    server.shutdown()
print('COMPACT_TRANSACTION_SUITE_COMPLETE',flush=True)
