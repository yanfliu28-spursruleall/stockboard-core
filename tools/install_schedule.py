"""注册 / 卸载 Windows 任务计划（每小时触发 refresh.py；真正是否构建由 refresh.py 按美股时段限频决定）。
  python tools/install_schedule.py              # 安装
  python tools/install_schedule.py --uninstall  # 卸载
  python tools/install_schedule.py --status     # 查看
说明：任务以当前用户“仅在登录时运行”，不需要保存 Windows 密码；电脑关机/休眠期间不会运行，开机后会补跑一次。"""
import getpass, os, subprocess, sys, tempfile
from pathlib import Path
from xml.sax.saxutils import escape

NAME = "USStockBoard-Refresh"
ROOT = Path(__file__).resolve().parent.parent
PYW = Path(sys.executable).with_name("pythonw.exe")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def sh(*args):
    r = subprocess.run(["schtasks", *args], capture_output=True, text=True, encoding="mbcs", errors="replace")
    return r.returncode, (r.stdout + r.stderr).strip()


def build_xml() -> str:
    user = f"{os.environ.get('USERDOMAIN', '')}\\{getpass.getuser()}".strip("\\")
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>US Stock Board 定时刷新：每小时触发，refresh.py 按美股时段限频，构建→加密→校验→替换 dist/</Description></RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <StartBoundary>2026-01-01T00:05:00</StartBoundary>
      <Repetition><Interval>PT1H</Interval></Repetition>
      <Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>
  <Principals><Principal id="Author"><UserId>{escape(user)}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <AllowHardTerminate>true</AllowHardTerminate>
    <ExecutionTimeLimit>PT1H</ExecutionTimeLimit>
    <Hidden>true</Hidden>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec><Command>{escape(str(PYW))}</Command><Arguments>"{escape(str(ROOT / 'refresh.py'))}"</Arguments><WorkingDirectory>{escape(str(ROOT))}</WorkingDirectory></Exec>
  </Actions>
</Task>"""


def main():
    if "--status" in sys.argv:
        print(sh("/Query", "/TN", NAME, "/V", "/FO", "LIST")[1]); return
    if "--uninstall" in sys.argv:
        print(sh("/Delete", "/TN", NAME, "/F")[1]); return
    if not PYW.exists():
        sys.exit(f"找不到 {PYW}")
    xml = Path(tempfile.gettempdir()) / "usboard_task.xml"
    xml.write_text(build_xml(), encoding="utf-16")
    code, out = sh("/Create", "/TN", NAME, "/XML", str(xml), "/F")
    xml.unlink(missing_ok=True)
    print(("已注册：" if code == 0 else "注册失败：") + out)
    sys.exit(code)


if __name__ == "__main__":
    main()
