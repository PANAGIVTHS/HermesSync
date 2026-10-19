#!/usr/bin/env python3
"""Targeted standalone regression for `hermes list/purge` and the `hermes <command>` CLI.
All paths confined to disposable TemporaryDirectory. Never touches real Hermes installs.
"""
import os, sys, tempfile, pathlib, subprocess, shutil, unittest, hashlib, json
SCRIPT=pathlib.Path(__file__).resolve().parent.parent / 'cli' / 'hermes_sync_v8.py'

def run(args, cwd, env, ok=True, inp=None):
    p=subprocess.run(args,cwd=cwd,env=env,text=True,input=inp,capture_output=True,timeout=35)
    if ok != (p.returncode==0):
        raise AssertionError(f"{args} => {p.returncode}\n{p.stdout}\n{p.stderr}")
    return p

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='hermes-v6-safe-')
        self.addCleanup(self.tmp.cleanup)
        self.root=pathlib.Path(self.tmp.name)
        self.install=self.root/'Hermes'
        self.src=self.root/'DesktopProject'
        self.src.mkdir()
        (self.src/'ALU.sv').write_text('before\n')
        self.env={**os.environ,'HERMES_INSTALL_DIR':str(self.install),'NO_COLOR':'1'}
        self.legacy=lambda *cmd,ok=True,inp=None: run([sys.executable,str(SCRIPT),*cmd],self.src,self.env,ok,inp)
        self.hermes_cli=self.root/'hermes'
        self.hermes_cli.symlink_to(SCRIPT)
        self.cli=lambda *cmd,ok=True,inp=None: run([sys.executable,str(self.hermes_cli),*cmd],self.src if self.src.is_dir() else self.root,self.env,ok,inp)
        self.agent=self.install/'workspaces'/'T'
        self.state=self.install/'sync'/'projects'/'T'

    def share(self):
        self.cli('share','--name','T','--yes')

    def test_a_namespace_and_sync(self):
        self.assertIn('Usage',self.cli().stdout)
        self.share()
        (self.agent/'new.txt').write_text('agent added\n')
        self.cli('sync')
        self.assertEqual((self.src/'new.txt').read_text(),'agent added\n')
        self.assertIn('Project: T',self.cli('status').stdout)

    def test_b_purge_cancelled_without_terminal(self):
        self.share()
        p=self.cli('purge',ok=False)
        self.assertIn('Confirmation required',p.stderr)
        self.assertTrue(self.agent.exists())
        self.assertTrue(self.state.exists())

    def test_c_purge_confirm_yes_irreversible(self):
        self.share()
        (self.agent/'dirty.txt').write_text('new')
        excluded=self.agent/'.env';excluded.write_text('secret')
        (self.agent/'local'/'deep').mkdir(parents=True)
        (self.agent/'local'/'deep'/'a.bin').write_bytes(b'blob')
        (self.agent/'point-to-source').symlink_to(self.src,target_is_directory=True)
        self.cli('purge','--yes')
        self.assertFalse(self.agent.exists())
        self.assertFalse(self.state.exists())
        self.assertEqual((self.src/'ALU.sv').read_text(),'before\n')
        self.assertIn('No registered projects',self.cli('list').stdout)

    def test_d_deleted_desktop_can_purge_by_name(self):
        self.share()
        shutil.rmtree(self.src)
        self.assertIn('desktop missing',self.cli('list').stdout)
        self.cli('purge','--project','T','--yes')
        self.assertFalse(self.agent.exists())
        self.assertFalse(self.state.exists())

    def test_e_original_git_untouched(self):
        run(['git','init','-q'],self.src,self.env)
        (self.src/'.git'/'hermes-sentinel').write_bytes(b'Git stays')
        self.share()
        self.cli('purge','--yes')
        self.assertEqual((self.src/'.git'/'hermes-sentinel').read_bytes(),b'Git stays')
        self.assertTrue((self.src/'ALU.sv').exists())

    def test_f_other_project_not_purged(self):
        self.share()
        other=self.root/'Other';other.mkdir();(other/'x').write_text('b')
        self.cli('share',str(other),'--name','Second','--yes')
        self.cli('purge','--yes')
        self.assertTrue((self.install/'workspaces'/'Second'/'x').exists())
        self.assertTrue((self.install/'sync'/'projects'/'Second'/'state.json').exists())

    def test_g_symlinked_agent_dir_refused(self):
        self.share()
        shutil.rmtree(self.agent)
        self.agent.symlink_to(self.src,target_is_directory=True)
        p=self.cli('purge','--yes',ok=False)
        self.assertIn('Unsafe project paths',p.stderr)
        self.assertTrue((self.src/'ALU.sv').exists())
        self.assertTrue(self.state.exists())

    def test_h_tampered_registration_refused(self):
        self.share()
        p=self.state/'state.json'
        state=json.loads(p.read_text());state['agent']=str(self.src)
        p.write_text(json.dumps(state))
        self.cli('purge','--yes',ok=False)
        self.assertTrue(self.agent.exists());self.assertTrue(self.src.exists())

    def test_i_pending_operation_refused(self):
        self.share()
        p=self.state/'state.json'
        state=json.loads(p.read_text());state['pending_operation']='sync'
        p.write_text(json.dumps(state))
        self.cli('purge','--yes',ok=False)
        self.assertTrue(self.agent.exists())

    def test_j_drop_is_not_purge(self):
        self.share()
        (self.agent/'ALU.sv').write_text('bad\n')
        self.cli('drop','--yes')
        self.assertTrue(self.state.exists())
        self.assertEqual((self.agent/'ALU.sv').read_text(),'before\n')

    def test_k_subcommand_help(self):
        self.assertIn('purge',self.cli('--help').stdout)
        self.assertIn('list',self.cli('--help').stdout)
        self.assertIn('project',self.cli('purge','--help').stdout)

if __name__=='__main__':
    unittest.main(verbosity=2)
