import platform
import subprocess

# True conferma l’esecuzione di osascript, non la visibilità del banner in macOS.
def notify(title, message):
    if platform.system() != 'Darwin':
        return False
    # Text travels as argv: asset names cannot inject AppleScript commands.
    script = 'on run argv\ndisplay notification (item 2 of argv) with title (item 1 of argv)\nend run'
    try:
        subprocess.run(['osascript', '-e', script, title, message], check=True, timeout=10, capture_output=True)
        return True
    except (OSError, subprocess.SubprocessError):
        return False
