import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import cv2
from PIL import Image, ImageTk
import threading
import time
import os

# ── Theme ──────────────────────────────────────────────────────────────────────
BG      = "#12121e"
SURFACE = "#1a1a2e"
FG      = "#d8d8e8"
MUTED   = "#55556a"

FONT_UI   = ("Segoe UI", 10)
FONT_MONO = ("Consolas", 9)
FONT_BTN  = ("Segoe UI", 10)


def _make_btn(parent, text, command, bg="#1e3a5f", width=None):
    kw = dict(
        text=text, command=command,
        bg=bg, fg=FG,
        activebackground="#2a5080", activeforeground="white",
        relief=tk.FLAT, padx=10, pady=5,
        font=FONT_BTN, cursor="hand2",
    )
    if width:
        kw["width"] = width
    return tk.Button(parent, **kw)


# ── Application ────────────────────────────────────────────────────────────────
class VideoFrameExtractor:
    STEP_SMALL = 1
    STEP_LARGE = 10

    def __init__(self, root):
        self.root = root
        self.root.title("Video Frame Extractor")
        self.root.geometry("960x680")
        self.root.minsize(640, 480)
        self.root.configure(bg=BG)

        # Video state
        self.cap = None
        self.video_path = None
        self.total_frames = 0
        self.fps = 30.0
        self.current_frame_idx = 0

        # Playback / seek state
        self.is_playing = False
        self._seek_dragging = False
        self._seek_was_playing = False
        self._photo = None          # keeps PhotoImage alive (prevents GC)
        self._last_frame = None     # last BGR frame for resize redraws

        self._build_ui()
        self._bind_keys()
        self.root.bind("<Configure>", self._on_resize)

    # ── UI construction ────────────────────────────────────────────────────────

    def _build_ui(self):
        # Top bar
        topbar = tk.Frame(self.root, bg=SURFACE, pady=8, padx=10)
        topbar.pack(fill=tk.X)

        _make_btn(topbar, "Open Video", self.open_video, bg="#0f3460").pack(side=tk.LEFT)

        self._title_lbl = tk.Label(
            topbar, text="No video loaded",
            bg=SURFACE, fg=MUTED, font=FONT_UI,
        )
        self._title_lbl.pack(side=tk.LEFT, padx=14)

        # Video canvas
        self._canvas = tk.Canvas(self.root, bg="#080808", highlightthickness=0)
        self._canvas.pack(fill=tk.BOTH, expand=True)
        self._hint_id = self._canvas.create_text(
            480, 300,
            text="Open a video file to get started\nCtrl+O",
            fill="#2a2a44", font=("Segoe UI", 15), justify=tk.CENTER,
        )

        # Time / frame info row
        info_row = tk.Frame(self.root, bg=BG, pady=3)
        info_row.pack(fill=tk.X, padx=12)

        self._time_lbl = tk.Label(
            info_row, text="--:--.--- / --:--.---",
            bg=BG, fg=MUTED, font=FONT_MONO,
        )
        self._time_lbl.pack(side=tk.LEFT)

        self._frame_lbl = tk.Label(
            info_row, text="Frame — / —",
            bg=BG, fg=MUTED, font=FONT_MONO,
        )
        self._frame_lbl.pack(side=tk.RIGHT)

        # Seekbar
        self._seek_var = tk.IntVar(value=0)

        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "V.Horizontal.TScale",
            troughcolor="#252540", background=BG,
            sliderlength=14, sliderrelief="flat",
        )

        self._seekbar = ttk.Scale(
            self.root, from_=0, to=1,
            variable=self._seek_var, orient=tk.HORIZONTAL,
            style="V.Horizontal.TScale",
            command=self._on_seek_drag,
        )
        self._seekbar.pack(fill=tk.X, padx=12, pady=(0, 4))
        self._seekbar.bind("<ButtonPress-1>",   self._on_seek_start)
        self._seekbar.bind("<ButtonRelease-1>", self._on_seek_end)

        # Control bar
        ctrl = tk.Frame(self.root, bg=SURFACE, pady=7)
        ctrl.pack(fill=tk.X)

        center = tk.Frame(ctrl, bg=SURFACE)
        center.pack(side=tk.LEFT, expand=True)

        _make_btn(center, "-10",  lambda: self.step_frame(-self.STEP_LARGE)).pack(side=tk.LEFT, padx=2)
        _make_btn(center, "◀ -1", lambda: self.step_frame(-self.STEP_SMALL)).pack(side=tk.LEFT, padx=2)

        self._play_btn = _make_btn(center, "▶  Play", self.toggle_play,
                                    bg="#1a5c30", width=9)
        self._play_btn.pack(side=tk.LEFT, padx=8)

        _make_btn(center, "+1 ▶", lambda: self.step_frame(self.STEP_SMALL)).pack(side=tk.LEFT, padx=2)
        _make_btn(center, "+10",  lambda: self.step_frame(self.STEP_LARGE)).pack(side=tk.LEFT, padx=2)

        _make_btn(ctrl, "Extract Frame  (Ctrl+S)", self.extract_frame,
                   bg="#7a1e1e").pack(side=tk.RIGHT, padx=12)

        # Status bar
        self._status_var = tk.StringVar(
            value="Ready  ·  Space = play/pause  ·  ← / → = step frame  ·  Shift+← / → = ±10 frames  ·  Ctrl+S = extract"
        )
        tk.Label(
            self.root, textvariable=self._status_var,
            bg="#0a0a14", fg="#383852",
            font=("Segoe UI", 8), anchor="w", padx=10, pady=3,
        ).pack(fill=tk.X, side=tk.BOTTOM)

    def _bind_keys(self):
        r = self.root
        r.bind("<space>",       lambda _e: self.toggle_play())
        r.bind("<Left>",        lambda _e: self.step_frame(-self.STEP_SMALL))
        r.bind("<Right>",       lambda _e: self.step_frame(self.STEP_SMALL))
        r.bind("<Shift-Left>",  lambda _e: self.step_frame(-self.STEP_LARGE))
        r.bind("<Shift-Right>", lambda _e: self.step_frame(self.STEP_LARGE))
        r.bind("<Control-o>",   lambda _e: self.open_video())
        r.bind("<Control-s>",   lambda _e: self.extract_frame())

    def _on_resize(self, _event):
        if self._last_frame is not None:
            self._render(self._last_frame)

    # ── File loading ───────────────────────────────────────────────────────────

    def open_video(self):
        path = filedialog.askopenfilename(
            title="Open Video",
            filetypes=[
                ("Video files",
                 "*.mp4 *.avi *.mov *.mkv *.wmv *.flv *.webm *.m4v *.ts *.mts"),
                ("All files", "*.*"),
            ],
        )
        if path:
            self._load(path)

    def _load(self, path):
        # Stop playback cleanly before swapping the capture object
        self.is_playing = False
        time.sleep(0.05)

        if self.cap:
            self.cap.release()

        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            messagebox.showerror("Error", f"Cannot open file:\n{path}")
            return

        self.cap = cap
        self.video_path = path
        self.total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        self.current_frame_idx = 0

        self._seekbar.config(to=max(1, self.total_frames - 1))
        self._seek_var.set(0)
        self._play_btn.config(text="▶  Play")

        name = os.path.basename(path)
        self.root.title(f"Video Frame Extractor — {name}")
        self._title_lbl.config(text=name, fg=FG)
        self._status_var.set(
            f"{name}  ·  {self.total_frames} frames  ·  {self.fps:.3f} fps"
        )

        self.show_frame(0)

    # ── Frame display ──────────────────────────────────────────────────────────

    def show_frame(self, idx):
        if not self.cap:
            return
        idx = max(0, min(int(idx), self.total_frames - 1))
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ret, frame = self.cap.read()
        if not ret:
            return
        self.current_frame_idx = idx
        self._last_frame = frame
        self._render(frame)
        self._sync_ui()

    def _render(self, bgr_frame):
        cw = self._canvas.winfo_width()  or 800
        ch = self._canvas.winfo_height() or 480
        if cw < 4 or ch < 4:
            return

        rgb = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]
        scale = min(cw / w, ch / h)
        nw = max(1, int(w * scale))
        nh = max(1, int(h * scale))
        rgb = cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_AREA)

        photo = ImageTk.PhotoImage(Image.fromarray(rgb))
        self._canvas.delete("all")
        self._canvas.create_image(cw // 2, ch // 2, anchor=tk.CENTER, image=photo)
        self._photo = photo  # must keep reference to avoid garbage collection

    def _sync_ui(self):
        cur = self.current_frame_idx / self.fps
        tot = self.total_frames / self.fps

        def fmt(s):
            m = int(s // 60)
            return f"{m:02d}:{s % 60:06.3f}"

        self._time_lbl.config(text=f"{fmt(cur)} / {fmt(tot)}")
        self._frame_lbl.config(
            text=f"Frame {self.current_frame_idx} / {self.total_frames - 1}"
        )
        if not self._seek_dragging:
            self._seek_var.set(self.current_frame_idx)

    # ── Playback ───────────────────────────────────────────────────────────────

    def toggle_play(self):
        if not self.cap:
            return
        if self.is_playing:
            self.is_playing = False
            self._play_btn.config(text="▶  Play")
        else:
            if self.current_frame_idx >= self.total_frames - 1:
                self.show_frame(0)
            self.is_playing = True
            self._play_btn.config(text="⏸  Pause")
            threading.Thread(target=self._play_loop, daemon=True).start()

    def _play_loop(self):
        while self.is_playing and self.cap:
            t0 = time.perf_counter()

            ret, frame = self.cap.read()
            if not ret:
                self.root.after(0, self._on_playback_end)
                return

            self.current_frame_idx = int(self.cap.get(cv2.CAP_PROP_POS_FRAMES)) - 1
            self._last_frame = frame

            # Schedule GUI updates on the main thread
            self.root.after(0, self._render, frame)
            self.root.after(0, self._sync_ui)

            elapsed = time.perf_counter() - t0
            time.sleep(max(0.0, 1.0 / self.fps - elapsed))

    def _on_playback_end(self):
        self.is_playing = False
        self._play_btn.config(text="▶  Play")

    def step_frame(self, delta):
        if not self.cap:
            return
        self.is_playing = False
        self._play_btn.config(text="▶  Play")
        self.show_frame(self.current_frame_idx + delta)

    # ── Seek bar ───────────────────────────────────────────────────────────────

    def _on_seek_start(self, _event):
        self._seek_dragging = True
        self._seek_was_playing = self.is_playing
        self.is_playing = False

    def _on_seek_drag(self, value):
        if self._seek_dragging:
            self.show_frame(int(float(value)))

    def _on_seek_end(self, _event):
        self.show_frame(int(self._seek_var.get()))
        self._seek_dragging = False
        if self._seek_was_playing:
            self.toggle_play()

    # ── Frame extraction ───────────────────────────────────────────────────────

    def extract_frame(self):
        if not self.cap:
            messagebox.showwarning("No Video", "Open a video file first.")
            return

        # Build a sensible default filename next to the source video
        if self.video_path:
            base = os.path.splitext(self.video_path)[0]
            default_name = f"{os.path.basename(base)}_frame_{self.current_frame_idx:06d}.png"
            default_dir  = os.path.dirname(base)
        else:
            default_name = f"frame_{self.current_frame_idx:06d}.png"
            default_dir  = os.getcwd()

        save_path = filedialog.asksaveasfilename(
            title="Save Frame As",
            initialfile=default_name,
            initialdir=default_dir,
            defaultextension=".png",
            filetypes=[
                ("PNG (lossless)", "*.png"),
                ("JPEG",           "*.jpg *.jpeg"),
                ("BMP",            "*.bmp"),
                ("TIFF",           "*.tiff *.tif"),
                ("All files",      "*.*"),
            ],
        )
        if not save_path:
            return

        # Seek to the exact frame index and read a fresh copy for export
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, self.current_frame_idx)
        ret, frame = self.cap.read()
        if not ret:
            messagebox.showerror("Error", "Could not read the frame from the video.")
            return

        ok = cv2.imwrite(save_path, frame)
        if ok:
            self._status_var.set(
                f"Saved frame {self.current_frame_idx}  →  {os.path.basename(save_path)}"
            )
            messagebox.showinfo(
                "Frame Saved",
                f"Frame {self.current_frame_idx} saved to:\n{save_path}",
            )
        else:
            messagebox.showerror("Error", "Failed to write the image file.")


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    root = tk.Tk()
    try:
        root.iconbitmap(default="")
    except Exception:
        pass
    VideoFrameExtractor(root)
    root.mainloop()


if __name__ == "__main__":
    main()
