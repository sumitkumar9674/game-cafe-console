"""Safe UI prototype for the Game Cafe Console lock screen."""

import tkinter as tk


control_window: tk.Tk | None = None
lock_overlay: tk.Toplevel | None = None


def _overlay_was_destroyed(event: tk.Event) -> None:
    """Clear our reference when Tkinter destroys the overlay window."""
    global lock_overlay

    if event.widget is lock_overlay:
        lock_overlay = None


def ignore_overlay_close_request() -> None:
    """Keep the overlay open when Windows requests that it close."""
    # Alt+F4 sends WM_DELETE_WINDOW, so handling it here prevents destruction.
    pass


def activate_lock() -> None:
    """Create the fullscreen test overlay if it is not already active."""
    global lock_overlay

    if lock_overlay is not None and lock_overlay.winfo_exists():
        lock_overlay.lift()
        return

    lock_overlay = tk.Toplevel(control_window)
    lock_overlay.title("Lock Lab - Test Lock")
    lock_overlay.configure(background="#171717")

    # A Toplevel is a separate window. Fullscreen makes it cover the current
    # screen without adding any operating-system restrictions.
    lock_overlay.attributes("-fullscreen", True)
    lock_overlay.protocol("WM_DELETE_WINDOW", ignore_overlay_close_request)
    lock_overlay.bind("<Destroy>", _overlay_was_destroyed)

    content = tk.Frame(lock_overlay, background="#171717")
    content.place(relx=0.5, rely=0.5, anchor="center")

    heading = tk.Label(
        content,
        text="TEST LOCK ACTIVE",
        font=("Segoe UI", 32, "bold"),
        foreground="white",
        background="#171717",
    )
    heading.pack(pady=(0, 16))

    message = tk.Label(
        content,
        text="This is a development prototype and does not restrict Windows.",
        font=("Segoe UI", 14),
        foreground="#d0d0d0",
        background="#171717",
    )
    message.pack(pady=(0, 28))

    tk.Button(content, text="UNLOCK TEST", command=deactivate_lock, width=22).pack(
        pady=6
    )
    tk.Button(content, text="KILL SOFTWARE", command=kill_software, width=22).pack(
        pady=6
    )

    lock_overlay.lift()
    lock_overlay.focus_force()


def deactivate_lock() -> None:
    """Remove the test overlay while leaving the control window running."""
    global lock_overlay

    if lock_overlay is not None:
        overlay_to_destroy = lock_overlay
        lock_overlay = None
        if overlay_to_destroy.winfo_exists():
            overlay_to_destroy.destroy()


def kill_software() -> None:
    """Close every Lock Lab window and end the Tkinter event loop."""
    deactivate_lock()
    if control_window is not None:
        control_window.destroy()


def main() -> None:
    """Build the developer controls and start Lock Lab."""
    global control_window

    control_window = tk.Tk()
    control_window.title("Lock Lab V2")
    control_window.geometry("320x210")
    control_window.resizable(False, False)
    control_window.protocol("WM_DELETE_WINDOW", kill_software)

    tk.Label(
        control_window,
        text="Lock Lab V2",
        font=("Segoe UI", 18, "bold"),
    ).pack(pady=(18, 12))

    tk.Button(
        control_window, text="ACTIVATE LOCK", command=activate_lock, width=24
    ).pack(pady=5)
    tk.Button(
        control_window, text="DEACTIVATE LOCK", command=deactivate_lock, width=24
    ).pack(pady=5)
    tk.Button(
        control_window, text="KILL SOFTWARE", command=kill_software, width=24
    ).pack(pady=5)

    control_window.mainloop()


if __name__ == "__main__":
    main()
