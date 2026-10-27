"""The windows of /cg-encrypt (Windows only): the recovery code, a Yes/No question, a hidden entry of the code. What a window shows or
takes never leaves it: a failure says what failed (the system's error number), never the text of the window, and a typed code comes
back only as the return value, never in a message, a printed line or an exception."""
import ctypes
import subprocess
import sys

from texts import t

MB_YESNO, MB_ICONWARNING, MB_SETFOREGROUND, MB_TOPMOST, IDYES = 0x4, 0x30, 0x10000, 0x40000, 6
CREATE_NEW_CONSOLE = 0x10
# the console entry: getpass reads the console itself, so it works with the output going to a pipe; the code goes out through the pipe only
_GETPASS = 'import getpass, sys; sys.stdout.buffer.write(getpass.getpass(sys.argv[1]).encode())'


def _box(text, flags):
    """The system's message box, always on top of the other windows; the button pressed."""
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.MessageBoxW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
    user32.MessageBoxW.restype = ctypes.c_int
    button = user32.MessageBoxW(None, text, t('dlg_title'), flags | MB_TOPMOST | MB_SETFOREGROUND)
    if not button:
        raise OSError('MessageBoxW failed: error %d' % ctypes.get_last_error())
    return button


def _window_code(tkinter, root):
    """The code typed into a window with a hidden field: the string, None when cancelled."""
    root.title(t('dlg_title'))
    root.attributes('-topmost', True)
    root.resizable(False, False)
    tkinter.Label(root, text=t('dlg_ask_code'), padx=12, pady=8).pack()
    entry = tkinter.Entry(root, show='•', width=48)
    entry.pack(padx=12)
    typed = []

    def ok(_=None):
        typed.append(entry.get())
        root.destroy()

    row = tkinter.Frame(root)
    row.pack(pady=8)
    tkinter.Button(row, text=t('dlg_ok'), width=10, command=ok).pack(side='left', padx=4)
    tkinter.Button(row, text=t('dlg_cancel'), width=10, command=root.destroy).pack(side='left', padx=4)
    root.bind('<Return>', ok)
    root.bind('<Escape>', lambda _: root.destroy())
    entry.focus_force()
    root.mainloop()  # closing the window by its cross is a cancel too
    return typed[0] if typed else None


def _console_code():
    """The code typed in a console of its own, for a Python without tkinter: closing it or Ctrl+C ends the child with an error, which is a
    cancel; the child's own errors go nowhere."""
    r = subprocess.run([sys.executable, '-c', _GETPASS, t('dlg_ask_code') + ' '], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                       creationflags=CREATE_NEW_CONSOLE)
    return r.stdout.decode('utf-8') if r.returncode == 0 else None


class WindowsUI:
    """The `ui` of encrypt.py: show_code(code), confirm(text) -> bool, ask_code() -> str | None (None: cancelled)."""

    def show_code(self, code):
        _box(t('dlg_code', code=code), 0)

    def confirm(self, text):
        return _box(text, MB_YESNO | MB_ICONWARNING) == IDYES

    def ask_code(self):
        try:
            import tkinter
            root = tkinter.Tk()
        except Exception:  # no tkinter, or no Tcl/Tk behind it (ImportError, TclError)
            return _console_code()
        return _window_code(tkinter, root)
