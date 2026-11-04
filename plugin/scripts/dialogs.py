"""The windows of /al-encrypt (Windows only): the recovery code, a Yes/No question, a hidden entry of the code. Also the folder window of
/al-index-dir. What a window shows or takes never leaves it: a failure says what failed (the system's error number), never the text of
the window, and a typed code comes back only as the return value, never in a message, a printed line or an exception."""
import ctypes
import os
import subprocess
import sys

from texts import t

MB_YESNO, MB_ICONWARNING, MB_DEFBUTTON2, MB_SETFOREGROUND, MB_TOPMOST, IDYES = 0x4, 0x30, 0x100, 0x10000, 0x40000, 6
CREATE_NEW_CONSOLE = 0x10
# the console entry: getpass reads the console itself, so it works with the output going to a pipe; the code goes out through the pipe only
_GETPASS = 'import getpass, sys; sys.stdout.buffer.write(getpass.getpass(sys.argv[1]).encode())'


def _box(text, flags, title=None):
    """The system's message box (with its own `title`, else the mod's), always on top of the other windows; the button pressed."""
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.MessageBoxW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
    user32.MessageBoxW.restype = ctypes.c_int
    button = user32.MessageBoxW(None, text, title or t('dlg_title'), flags | MB_TOPMOST | MB_SETFOREGROUND)
    if not button:
        raise OSError('MessageBoxW failed: error %d' % ctypes.get_last_error())
    return button


CF_UNICODETEXT, GMEM_MOVEABLE = 13, 2


def _clipboard_set(code):
    """The code, and only the code, on the clipboard as text, marked to stay out of the Win+V history and the cloud clipboard (those formats
    go in first: if one fails, nothing of the code is out there). The clipboard's sequence number right after, for _clipboard_clear. An
    OSError names the call that failed and the system's error number, never the code."""
    user32, kernel32 = ctypes.WinDLL('user32', use_last_error=True), ctypes.WinDLL('kernel32', use_last_error=True)
    c_void, c_uint, c_size = ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t
    for fn, args, res in ((user32.OpenClipboard, [c_void], ctypes.c_int), (user32.CloseClipboard, [], ctypes.c_int),
                          (user32.EmptyClipboard, [], ctypes.c_int), (user32.RegisterClipboardFormatW, [ctypes.c_wchar_p], c_uint),
                          (user32.SetClipboardData, [c_uint, c_void], c_void), (user32.GetClipboardSequenceNumber, [], ctypes.c_uint32),
                          (kernel32.GlobalAlloc, [c_uint, c_size], c_void), (kernel32.GlobalLock, [c_void], c_void),
                          (kernel32.GlobalUnlock, [c_void], ctypes.c_int), (kernel32.GlobalFree, [c_void], c_void)):
        fn.argtypes, fn.restype = args, res

    def fail(what):
        raise OSError('%s failed: error %d' % (what, ctypes.get_last_error()))

    def put(fmt, data):
        mem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data)) or fail('GlobalAlloc')
        ptr = kernel32.GlobalLock(mem)
        if not ptr:
            kernel32.GlobalFree(mem)
            fail('GlobalLock')
        ctypes.memmove(ptr, data, len(data))
        kernel32.GlobalUnlock(mem)
        if not user32.SetClipboardData(fmt, mem):  # on success the system owns the memory
            kernel32.GlobalFree(mem)
            fail('SetClipboardData')

    if not user32.OpenClipboard(None):
        fail('OpenClipboard')
    try:
        if not user32.EmptyClipboard():
            fail('EmptyClipboard')
        for name, data in (('ExcludeClipboardContentFromMonitorProcessing', b'\0\0\0\0'), ('CanIncludeInClipboardHistory', b'\0\0\0\0'),
                           ('CanUploadToCloudClipboard', b'\0\0\0\0')):
            put(user32.RegisterClipboardFormatW(name) or fail('RegisterClipboardFormatW'), data)
        put(CF_UNICODETEXT, (code + '\0').encode('utf-16-le'))
    finally:
        user32.CloseClipboard()
    return user32.GetClipboardSequenceNumber()


def _clipboard_clear(seq):
    """Empties the clipboard only if it is still what _clipboard_set left (its sequence number is `seq`): the text is never read back."""
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.GetClipboardSequenceNumber.restype = ctypes.c_uint32
    user32.OpenClipboard.argtypes = [ctypes.c_void_p]
    if user32.GetClipboardSequenceNumber() != seq:
        return
    if not user32.OpenClipboard(None):
        raise OSError('OpenClipboard failed: error %d' % ctypes.get_last_error())
    try:
        user32.EmptyClipboard()
    finally:
        user32.CloseClipboard()


def _window_show_code(tkinter, root, code):
    """The recovery code in a window: the explanation, the code in a field that can be selected but not changed, Copy and OK. Closing it (OK,
    the cross, Escape) empties the clipboard if our code is still there. A clipboard failure is told in the window, which stays."""
    root.title(t('dlg_title'))
    root.attributes('-topmost', True)
    root.resizable(False, False)
    tkinter.Label(root, text=t('dlg_code_head'), padx=12, pady=8, justify='left', wraplength=460).pack()
    entry = tkinter.Entry(root, width=len(code) + 2, justify='center')
    entry.insert(0, code)
    entry.config(state='readonly')
    entry.pack(padx=12)
    tkinter.Label(root, text=t('dlg_code_tail'), padx=12, pady=8, justify='left', wraplength=460).pack()
    status = tkinter.Label(root, text='', padx=12)
    status.pack()
    seq = []

    def copy():
        try:
            seq[:] = [_clipboard_set(code)]
            status.config(text='')
        except OSError as e:  # the message holds the failed call and its error number only
            status.config(text=str(e))

    def close(_=None):
        try:
            if seq:
                _clipboard_clear(seq[0])
        finally:
            root.destroy()

    row = tkinter.Frame(root)
    row.pack(pady=8)
    tkinter.Button(row, text=t('dlg_copy'), width=10, command=copy).pack(side='left', padx=4)
    tkinter.Button(row, text=t('dlg_ok'), width=10, command=close).pack(side='left', padx=4)
    root.protocol('WM_DELETE_WINDOW', close)
    root.bind('<Return>', close)
    root.bind('<Escape>', close)
    entry.focus_force()
    root.mainloop()


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
    cancel; the child's own errors go nowhere. -I: with -c the folder this runs from would be searched for modules first, and a getpass.py
    there would be handed the code. pythonw.exe gets no console of its own, so the python.exe beside it runs the entry."""
    head, tail = os.path.split(sys.executable)
    python = os.path.join(head, 'python.exe') if tail.lower() == 'pythonw.exe' else sys.executable
    r = subprocess.run([python, '-I', '-c', _GETPASS, t('dlg_ask_code') + ' '], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                       creationflags=CREATE_NEW_CONSOLE)
    return r.stdout.decode('utf-8') if r.returncode == 0 else None


def pick_folder(title):
    """The system's window for choosing a folder, hidden parent and on top of the others: the folder, None when cancelled. Without tkinter
    (or Tcl/Tk behind it) there is no window, and the answer asks for the path in the command."""
    try:
        import tkinter
        from tkinter import filedialog
        root = tkinter.Tk()
    except Exception:  # ImportError, TclError
        raise ValueError(t('index_dir_no_picker')) from None
    try:
        root.withdraw()
        root.attributes('-topmost', True)
        return filedialog.askdirectory(parent=root, title=title) or None  # a cancelled window gives back an empty string
    finally:
        root.destroy()


class WindowsUI:
    """The `ui` of encrypt.py: show_code(code), confirm(text) -> bool, ask_code() -> str | None (None: cancelled),
    and of indexdir.py: pick_folder() -> str | None (None: cancelled)."""

    def show_code(self, code):
        try:
            import tkinter
            root = tkinter.Tk()
        except Exception:  # no tkinter, or no Tcl/Tk behind it (ImportError, TclError): the box has no Copy button, its text says Ctrl+C
            return _box(t('dlg_code', code=code), 0)
        _window_show_code(tkinter, root, code)

    def confirm(self, text, title=None):
        # No is the default button: an Enter held down to close the box before closes this one too, and must never answer Yes
        return _box(text, MB_YESNO | MB_ICONWARNING | MB_DEFBUTTON2, title=title) == IDYES

    def ask_code(self):
        try:
            import tkinter
            root = tkinter.Tk()
        except Exception:  # no tkinter, or no Tcl/Tk behind it (ImportError, TclError)
            return _console_code()
        return _window_code(tkinter, root)

    def pick_folder(self):
        return pick_folder(t('dlg_pick_index_dir'))
