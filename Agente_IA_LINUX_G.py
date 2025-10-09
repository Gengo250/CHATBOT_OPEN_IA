import os
import io
import base64
import platform
import subprocess
from pathlib import Path
import json
import logging
import threading
import tkinter as tk
from tkinter import filedialog, messagebox
from tkinter import ttk
from PIL import Image, ImageTk
import requests

# --------- Config ---------
MODEL = "gpt-4o-mini"
API_URL = "https://api.openai.com/v1/chat/completions"

# Chave: SENHA.py > var de ambiente
try:
    from SENHA import API_KEY as OPENAI_API_KEY  
except Exception:
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


# --------- Utils ---------
def get_downloads_dir() -> str:
    if platform.system() == "Linux":
        try:
            out = subprocess.run(["xdg-user-dir", "DOWNLOAD"], capture_output=True, text=True, check=True)
            p = out.stdout.strip()
            if p and os.path.isdir(p):
                return p
        except Exception:
            pass
    home = Path.home()
    for cand in [home / "Downloads", home / "Download", home / "Baixados"]:
        if cand.is_dir():
            return str(cand)
    return str(home)


def image_to_data_url(pil_img: Image.Image, max_w=1280, max_h=1280, fmt="JPEG", quality=85) -> str:
    w, h = pil_img.size
    scale = min(max_w / w, max_h / h, 1.0)
    if scale < 1.0:
        pil_img = pil_img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    if pil_img.mode not in ("RGB", "L"):
        pil_img = pil_img.convert("RGB")

    buf = io.BytesIO()
    if fmt.upper() == "PNG":
        pil_img.save(buf, format="PNG", optimize=True)
        mime = "image/png"
    else:
        pil_img.save(buf, format="JPEG", quality=quality, optimize=True)
        mime = "image/jpeg"

    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:{mime};base64,{b64}"


def trim_history(messages, max_turns=6):
    """
    Mantém: 1x system + últimos 'max_turns' pares (user/assistant).
    Evita estouro de contexto. Imagens já foram enviadas e não precisam ser repetidas.
    """
    if not messages:
        return messages
    system = [m for m in messages if m.get("role") == "system"][:1]
    dialog = [m for m in messages if m.get("role") != "system"]
    # pega do fim para o início
    keep = dialog[-(max_turns * 2):] if len(dialog) > (max_turns * 2) else dialog
    return system + keep


# --------- App ---------
class ChatApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Chatbot (Texto + Imagem)")
        self.geometry("900x700")

        if not OPENAI_API_KEY:
            messagebox.showwarning(
                "Chave ausente",
                "Defina sua chave em SENHA.py (API_KEY) ou na variável de ambiente OPENAI_API_KEY."
            )

        self.initial_dir = get_downloads_dir()
        self.pending_images = []   # PIL Images para a PRÓXIMA mensagem
        self.history = [
            {"role": "system", "content": "Você é um assistente útil. Responda em português do Brasil sempre que possível."}
        ]

        self._build_ui()

    # ---------- UI ----------
    def _build_ui(self):
        container = ttk.Frame(self)
        container.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self.canvas = tk.Canvas(container, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(container, orient="vertical", command=self.canvas.yview)
        self.chat_frame = ttk.Frame(self.canvas)

        self.chat_frame.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.create_window((0, 0), window=self.chat_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        bottom = ttk.Frame(self)
        bottom.pack(fill=tk.X, padx=8, pady=(0, 8))

        self.entry = tk.Text(bottom, height=3, wrap=tk.WORD)
        self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        self.entry.bind("<Control-Return>", lambda e: self.on_send())  # Ctrl+Enter envia

        btn_col = ttk.Frame(bottom)
        btn_col.pack(side=tk.RIGHT)

        self.btn_add_img = ttk.Button(btn_col, text="Anexar imagem", command=self.on_add_image)
        self.btn_add_img.pack(fill=tk.X, pady=(0, 6))

        self.btn_send = ttk.Button(btn_col, text="Enviar", command=self.on_send)
        self.btn_send.pack(fill=tk.X)

        bottom2 = ttk.Frame(self)
        bottom2.pack(fill=tk.X, padx=8, pady=(0, 10))

        self.lbl_pending = ttk.Label(bottom2, text="Nenhuma imagem anexada.")
        self.lbl_pending.pack(side=tk.LEFT)

        self.btn_clear = ttk.Button(bottom2, text="Nova conversa", command=self.on_clear)
        self.btn_clear.pack(side=tk.RIGHT)

        self._add_assistant_bubble("Oi! Envie uma pergunta, anexe imagens se quiser, e eu respondo 😉")

    def _add_bubble(self, text, role="user"):
        bg = "#e8f0fe" if role == "assistant" else "#eaeaea"
        fg = "#111111"
        frame = ttk.Frame(self.chat_frame)
        frame.pack(anchor="w" if role == "assistant" else "e", pady=4, fill=tk.X)

        bubble = tk.Label(frame, text=text, bg=bg, fg=fg, justify=tk.LEFT, wraplength=780, padx=10, pady=8)
        bubble.pack(anchor="w" if role == "assistant" else "e")
        self._scroll_to_end()

    def _add_assistant_bubble(self, text): self._add_bubble(text, "assistant")
    def _add_user_bubble(self, text): self._add_bubble(text, "user")

    def _add_image_preview(self, pil_img, role="user"):
        img = pil_img.copy()
        img.thumbnail((320, 320), Image.LANCZOS)
        tkimg = ImageTk.PhotoImage(img)
        frame = ttk.Frame(self.chat_frame)
        frame.pack(anchor="e" if role == "user" else "w", pady=4)
        lbl = tk.Label(frame, image=tkimg, bd=1, relief="solid")
        lbl.image = tkimg
        lbl.pack()
        self._scroll_to_end()

    def _scroll_to_end(self):
        self.update_idletasks()
        self.canvas.yview_moveto(1.0)

    # ---------- Ações ----------
    def on_add_image(self):
        filetypes = [
            ("Imagens", ("*.png", "*.jpg", "*.jpeg", "*.bmp", "*.gif", "*.tiff", "*.webp", "*.ppm", "*.pgm", "*.pbm")),
            ("Todos os arquivos", "*"),
        ]
        paths = filedialog.askopenfilenames(
            title="Selecione imagem(ns)",
            initialdir=self.initial_dir,
            filetypes=filetypes
        )
        if not paths:
            return

        added = 0
        for p in paths:
            try:
                # Copia e fecha imediatamente o handle do arquivo
                with Image.open(p) as im:
                    img = im.copy()
                self.pending_images.append(img)
                self._add_image_preview(img, role="user")
                added += 1
            except Exception as e:
                logging.warning(f"Falha ao carregar {p}: {e}")

        self.lbl_pending.config(
            text=f"{len(self.pending_images)} imagem(ns) anexada(s) para a próxima mensagem."
            if added else "Nenhuma imagem anexada."
        )

    def on_send(self):
        user_text = self.entry.get("1.0", tk.END).strip()
        if not user_text and not self.pending_images:
            messagebox.showinfo("Atenção", "Digite uma mensagem ou anexe ao menos uma imagem.")
            return

        if user_text:
            self._add_user_bubble(user_text)

        user_content = []
        if user_text:
            user_content.append({"type": "text", "text": user_text})

        for pil_img in self.pending_images:
            data_url = image_to_data_url(pil_img, max_w=1280, max_h=1280, fmt="JPEG", quality=85)
            user_content.append({"type": "image_url", "image_url": {"url": data_url}})

        self.history.append({"role": "user", "content": user_content})
        self.history = trim_history(self.history, max_turns=6)

        # limpa UI e trava botões
        self.entry.delete("1.0", tk.END)
        self.pending_images.clear()
        self.lbl_pending.config(text="Nenhuma imagem anexada.")
        self.btn_send.config(state=tk.DISABLED)
        self.btn_add_img.config(state=tk.DISABLED)

        # Chamada em thread para não travar a UI
        threading.Thread(target=self._call_openai_thread, daemon=True).start()

    def _call_openai_thread(self):
        try:
            headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}
            payload = {
                "model": MODEL,
                "messages": self.history,
                "temperature": 0.7,
                "max_tokens": 700
            }

            logging.info("Enviando requisição ao modelo...")
            resp = requests.post(API_URL, headers=headers, json=payload, timeout=120)
            resp.raise_for_status()
            data = resp.json()

            if "choices" not in data or not data["choices"]:
                raise ValueError("Resposta da API sem 'choices'.")

            answer = data["choices"][0]["message"]["content"]
            # Atualiza estado e UI na thread principal
            self.after(0, self._finish_with_answer, answer)

        except requests.exceptions.HTTPError as e:
            msg = f"HTTP {e.response.status_code}: {e.response.text[:300]}"
            logging.error(msg)
            self.after(0, lambda: messagebox.showerror("Erro HTTP", msg))
            self.after(0, self._enable_buttons)
        except requests.exceptions.RequestException as e:
            logging.error(f"Erro de rede: {e}")
            self.after(0, lambda: messagebox.showerror("Erro de rede", str(e)))
            self.after(0, self._enable_buttons)
        except Exception as e:
            logging.error(f"Erro geral: {e}")
            self.after(0, lambda: messagebox.showerror("Erro", str(e)))
            self.after(0, self._enable_buttons)

    def _finish_with_answer(self, answer: str):
        self.history.append({"role": "assistant", "content": answer})
        self._add_assistant_bubble(answer)
        self._enable_buttons()

    def _enable_buttons(self):
        self.btn_send.config(state=tk.NORMAL)
        self.btn_add_img.config(state=tk.NORMAL)

    def on_clear(self):
        for w in self.chat_frame.winfo_children():
            w.destroy()
        self._add_assistant_bubble("Conversa limpa. Como posso ajudar?")
        self.history = [
            {"role": "system", "content": "Você é um assistente útil. Responda em português do Brasil sempre que possível."}
        ]
        self.pending_images.clear()
        self.lbl_pending.config(text="Nenhuma imagem anexada.")
        self.entry.delete("1.0", tk.END)


if __name__ == "__main__":
    try:
        app = ChatApp()
        app.mainloop()
    except Exception:
        logging.exception("Falha ao iniciar a aplicação")
        raise
