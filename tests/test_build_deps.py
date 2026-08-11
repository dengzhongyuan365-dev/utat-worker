from pathlib import Path

from utat.runner.base import TaskRunner


class DummyRunner(TaskRunner):
    def run(self):
        raise NotImplementedError

    def run_process(self, cmd, *, cwd: Path, log_path: Path, env=None, shell=False, phase="running"):
        self.seen_cmd = cmd
        self.seen_env = env or {}
        log_path.write_text(str(cmd), encoding="utf-8")
        return 0


def test_install_build_deps_uses_sudo_stdin_password(tmp_path):
    root = tmp_path / "repo"
    (root / "debian").mkdir(parents=True)
    (root / "debian" / "control").write_text("Source: x\n", encoding="utf-8")
    runner = DummyRunner({"id": "t1", "dependency_command": "sudo apt build-dep -y .", "environment": {}}, tmp_path / "task")
    rc, _ = runner.install_build_deps(root)
    assert rc == 0
    assert "sudo -S" in runner.seen_cmd[2]
    assert "INSTALL_PASSWORD" in runner.seen_env
    assert runner.seen_env["INSTALL_PASSWORD"] == "1"


def test_full_mode_defaults_package_and_install_commands(tmp_path):
    root = tmp_path / "repo"
    (root / "debian").mkdir(parents=True)
    (root / "debian" / "control").write_text("Source: x\n", encoding="utf-8")
    runner = DummyRunner({"id": "t1", "repo": "https://example.invalid/deepin-voice-note", "execution_mode": "full", "environment": {}}, tmp_path / "task2")
    package_cmd = runner.build_command_for("package_command", root)
    install_cmd = runner.build_command_for("install_command", root)
    assert "dpkg-buildpackage -us -uc -b -j$(nproc)" in package_cmd
    assert ".utat-generated-debs" in package_cmd
    assert "apt install -y" in install_cmd
    assert "command -v deepin-voice-note" in install_cmd


def test_install_command_sudo_is_noninteractive(tmp_path):
    runner = DummyRunner({"id": "t1", "environment": {}}, tmp_path / "task3")
    env = {}
    cmd = runner.with_noninteractive_sudo("test -s .utat-generated-debs && sudo apt install -y ../x.deb", env, allow_sudo=True)
    assert "sudo -S" in cmd
    assert env["INSTALL_PASSWORD"] == "1"


def test_full_mode_overrides_bare_dpkg_package_command(tmp_path):
    root = tmp_path / "repo"
    (root / "debian").mkdir(parents=True)
    (root / "debian" / "control").write_text("Source: x\n", encoding="utf-8")
    runner = DummyRunner({
        "id": "t1",
        "repo": "https://example.invalid/deepin-image-viewer",
        "execution_mode": "full",
        "package_command": "dpkg-buildpackage -us -uc -b -j$(nproc)",
        "environment": {},
    }, tmp_path / "task4")
    package_cmd = runner.build_command_for("package_command", root)
    assert "dpkg-buildpackage -us -uc -b -j$(nproc)" in package_cmd
    assert ".utat-generated-debs" in package_cmd


def test_full_mode_overrides_glob_deb_install_command(tmp_path):
    root = tmp_path / "repo"
    (root / "debian").mkdir(parents=True)
    (root / "debian" / "control").write_text("Source: x\n", encoding="utf-8")
    runner = DummyRunner({
        "id": "t1",
        "repo": "https://example.invalid/deepin-image-viewer",
        "execution_mode": "full",
        "install_command": "sudo apt install -y ./deepin-image-viewer_*.deb && command -v deepin-image-viewer",
        "environment": {},
    }, tmp_path / "task5")
    install_cmd = runner.build_command_for("install_command", root)
    assert "./deepin-image-viewer_*.deb" not in install_cmd
    assert "cat .utat-generated-debs | xargs" not in install_cmd
    assert "apt install -y $debs" in install_cmd
    assert "sudo -S" in install_cmd
    assert "${INSTALL_PASSWORD:-1}" in install_cmd
