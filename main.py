"""
Card Printer Pro - Vanguard Edition
Versione Android (Kivy)
"""

import os
import json
import threading
import tempfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- Kivy imports ---
from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.togglebutton import ToggleButton
from kivy.uix.slider import Slider
from kivy.uix.progressbar import ProgressBar
from kivy.uix.popup import Popup
from kivy.uix.textinput import TextInput
from kivy.uix.spinner import Spinner
from kivy.uix.checkbox import CheckBox
from kivy.uix.filechooser import FileChooserListView
from kivy.clock import Clock
from kivy.metrics import dp
from kivy.core.window import Window
from kivy.utils import get_color_from_hex

# PDF & Image
from fpdf import FPDF
from PIL import Image as PILImage

# Android-specific (solo se su Android)
try:
    from android.permissions import request_permissions, Permission
    from android.storage import primary_external_storage_path
    ANDROID = True
except ImportError:
    ANDROID = False

# ---------------- Parametri ----------------
CARD_WIDTH_MM  = 59.0
CARD_HEIGHT_MM = 86.0
GAP_MM         = 5.0
PAGE_W         = 210
PAGE_H         = 297

CONFIG_FILE = "card_printer_config.json"

PDF_FORMATS = {
    "PDF Standard":                  {"name": "Standard",  "version": "1.4"},
    "PDF/A-1b (Archiviazione)":      {"name": "PDF/A-1b",  "version": "1.4"},
    "PDF/X-1a (Stampa CMYK)":        {"name": "PDF/X-1a",  "version": "1.3"},
    "PDF/X-3 (Stampa ICC)":          {"name": "PDF/X-3",   "version": "1.3"},
    "PDF/X-4 (Trasparenze)":         {"name": "PDF/X-4",   "version": "1.6"},
}

# ===================== LOGICA CORE (invariata) =====================

def mm_to_px(mm, dpi):
    return int(mm / 25.4 * dpi)


def draw_crop_marks(pdf, x, y, w, h, mark_len=3):
    pdf.set_line_width(0.1)
    pdf.line(x,     y,     x + mark_len, y)
    pdf.line(x,     y,     x,            y + mark_len)
    pdf.line(x + w, y,     x + w - mark_len, y)
    pdf.line(x + w, y,     x + w,        y + mark_len)
    pdf.line(x,     y + h, x + mark_len, y + h)
    pdf.line(x,     y + h, x,            y + h - mark_len)
    pdf.line(x + w, y + h, x + w - mark_len, y + h)
    pdf.line(x + w, y + h, x + w,        y + h - mark_len)


def list_image_files(folder):
    exts = (".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif")
    return sorted([
        entry.path for entry in os.scandir(folder)
        if entry.is_file() and entry.name.lower().endswith(exts)
    ])


def process_image_to_temp(img_path, target_w, target_h):
    """Ridimensiona immagine con Pillow e salva in file temporaneo."""
    try:
        img = PILImage.open(img_path)
        w, h = img.size
        scale = min(target_w / w, target_h / h, 1.0)
        if scale < 1.0:
            new_w = int(w * scale)
            new_h = int(h * scale)
            img = img.resize((new_w, new_h), PILImage.LANCZOS)

        # Converti in RGB se necessario (FPDF non vuole RGBA)
        if img.mode in ("RGBA", "P"):
            bg = PILImage.new("RGB", img.size, (255, 255, 255))
            if img.mode == "P":
                img = img.convert("RGBA")
            bg.paste(img, mask=img.split()[3] if img.mode == "RGBA" else None)
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
        tmp.close()
        img.save(tmp.name, "JPEG", quality=92, optimize=True)
        return tmp.name
    except Exception as e:
        print(f"Errore processing {img_path}: {e}")
        return None


def compute_grid_positions(page_w, page_h, card_w, card_h, gap):
    cols = max(1, int((page_w + gap) // (card_w + gap)))
    rows = max(1, int((page_h + gap) // (card_h + gap)))
    grid_w = cols * card_w + (cols - 1) * gap
    grid_h = rows * card_h + (rows - 1) * gap
    x_start = (page_w - grid_w) / 2
    y_start = (page_h - grid_h) / 2
    positions = []
    for r in range(rows):
        y = y_start + r * (card_h + gap)
        for c in range(cols):
            x = x_start + c * (card_w + gap)
            positions.append((x, y))
    return positions


def apply_pdf_format(pdf, pdf_format):
    info = PDF_FORMATS.get(pdf_format, PDF_FORMATS["PDF Standard"])
    version_map = {"1.3": "1.3", "1.6": "1.6"}
    pdf.pdf_version = version_map.get(info["version"], "1.4")
    if "PDF/X" in pdf_format:
        pdf.set_creator("Card Printer Pro")
        pdf.set_title("Carte Vanguard - Stampa")
    elif "PDF/A" in pdf_format:
        pdf.set_creator("Card Printer Pro")
        pdf.set_title("Carte Vanguard - Archiviazione")
    return pdf


def make_pdf(image_folder, output_pdf, logo_path, progress_callback,
             dpi, card_w, card_h, gap, show_crop_marks, workers,
             include_back, pdf_format):
    images = list_image_files(image_folder)
    if not images:
        return False, "Nessuna immagine trovata!"

    card_w_px = mm_to_px(card_w, dpi)
    card_h_px = mm_to_px(card_h, dpi)
    positions  = compute_grid_positions(PAGE_W, PAGE_H, card_w, card_h, gap)
    slots      = len(positions)
    total      = len(images)

    temp_files = [None] * total
    progress_callback(0, f"Elaborazione {total} immagini...")

    with ThreadPoolExecutor(max_workers=workers) as ex:
        future_map = {
            ex.submit(process_image_to_temp, images[i], card_w_px, card_h_px): i
            for i in range(total)
        }
        done = 0
        for fut in as_completed(future_map):
            idx = future_map[fut]
            tmp = fut.result()
            if tmp:
                temp_files[idx] = tmp
            done += 1
            progress_callback(done / total * 50, f"Processate {done}/{total}")

    temp_files = [f for f in temp_files if f]

    pdf = FPDF(unit="mm", format="A4")
    pdf = apply_pdf_format(pdf, pdf_format)
    pdf.set_auto_page_break(False)
    pdf.set_compression(True)

    chunks = [temp_files[i:i + slots] for i in range(0, len(temp_files), slots)]

    if include_back:
        total_steps   = len(chunks) * 2
        processed     = 0
        for chunk in chunks:
            # --- RETRO ---
            pdf.add_page()
            for si, pos in enumerate(positions):
                if si >= len(chunk):
                    break
                x_b = PAGE_W - pos[0] - card_w
                pdf.image(logo_path, x=x_b, y=pos[1], w=card_w, h=card_h)
            processed += 1
            progress_callback(50 + processed / total_steps * 25,
                              f"Pagina retro {processed}")
            # --- FRONTE ---
            pdf.add_page()
            for si, pos in enumerate(positions):
                if si >= len(chunk):
                    break
                pdf.image(chunk[si], x=pos[0], y=pos[1], w=card_w, h=card_h)
                if show_crop_marks:
                    draw_crop_marks(pdf, pos[0], pos[1], card_w, card_h)
            processed += 1
            progress_callback(50 + processed / total_steps * 48,
                              f"Pagina fronte {processed}")
        mode_msg = "duplex"
    else:
        total_steps = len(chunks)
        for i, chunk in enumerate(chunks):
            pdf.add_page()
            for si, pos in enumerate(positions):
                if si >= len(chunk):
                    break
                pdf.image(chunk[si], x=pos[0], y=pos[1], w=card_w, h=card_h)
                if show_crop_marks:
                    draw_crop_marks(pdf, pos[0], pos[1], card_w, card_h)
            progress_callback(50 + (i + 1) / total_steps * 45,
                              f"Pagina {i + 1}/{total_steps}")
        mode_msg = "solo fronte"

    progress_callback(95, "Salvataggio PDF...")
    pdf.output(output_pdf)

    for f in temp_files:
        try:
            os.remove(f)
        except Exception:
            pass

    progress_callback(100, "Completato!")
    fmt_name = PDF_FORMATS[pdf_format]["name"]
    return True, f"PDF creato ({mode_msg}, {fmt_name}): {len(chunks)} pag., {len(temp_files)} carte"


# ===================== INTERFACCIA KIVY =====================

COLORS = {
    "red":        get_color_from_hex("#c0392b"),
    "red_dark":   get_color_from_hex("#96281b"),
    "green":      get_color_from_hex("#27ae60"),
    "orange":     get_color_from_hex("#e67e22"),
    "bg":         get_color_from_hex("#f5f5f5"),
    "white":      get_color_from_hex("#ffffff"),
    "text":       get_color_from_hex("#2c3e50"),
    "gray":       get_color_from_hex("#7f8c8d"),
    "lightgray":  get_color_from_hex("#ecf0f1"),
}


def make_label(text, font_size=14, bold=False, color=None, **kwargs):
    lbl = Label(
        text=text,
        font_size=dp(font_size),
        bold=bold,
        color=color or COLORS["text"],
        halign="left",
        valign="middle",
        **kwargs,
    )
    lbl.bind(size=lbl.setter("text_size"))
    return lbl


def make_button(text, callback, bg=None, font_size=14, **kwargs):
    btn = Button(
        text=text,
        font_size=dp(font_size),
        background_color=bg or COLORS["red"],
        color=COLORS["white"],
        size_hint_y=None,
        height=dp(48),
        **kwargs,
    )
    btn.bind(on_press=callback)
    return btn


class SectionBox(BoxLayout):
    """Contenitore con titolo stile LabelFrame."""
    def __init__(self, title, **kwargs):
        super().__init__(orientation="vertical", spacing=dp(6),
                         size_hint_y=None, **kwargs)
        self.bind(minimum_height=self.setter("height"))
        title_lbl = Label(
            text=f"[b]{title}[/b]",
            markup=True,
            font_size=dp(13),
            color=COLORS["red"],
            size_hint_y=None,
            height=dp(28),
            halign="left",
        )
        title_lbl.bind(size=title_lbl.setter("text_size"))
        self.add_widget(title_lbl)


class FileChooserPopup(Popup):
    """Popup per selezionare file o cartella."""
    def __init__(self, callback, select_dir=False, filters=None, **kwargs):
        super().__init__(**kwargs)
        self.callback     = callback
        self.select_dir   = select_dir
        self.title        = "Seleziona cartella" if select_dir else "Seleziona file"
        self.size_hint    = (0.95, 0.9)
        self.title_color  = COLORS["text"]

        layout = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(8))

        start_path = "/"
        if ANDROID:
            try:
                start_path = primary_external_storage_path()
            except Exception:
                pass

        self.chooser = FileChooserListView(
            path=start_path,
            dirselect=select_dir,
            filters=filters or [],
        )
        layout.add_widget(self.chooser)

        btn_row = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        btn_row.add_widget(make_button("Annulla", lambda *_: self.dismiss(),
                                       bg=COLORS["gray"]))
        btn_row.add_widget(make_button("Seleziona", self._select))
        layout.add_widget(btn_row)
        self.content = layout

    def _select(self, *_):
        sel = self.chooser.selection
        if sel:
            self.callback(sel[0])
            self.dismiss()


class CardPrinterRoot(BoxLayout):
    """Widget root dell'app."""

    def __init__(self, **kwargs):
        super().__init__(orientation="vertical", **kwargs)

        # ---- stato ----
        self.image_folder  = ""
        self.logo_path     = ""
        self.output_path   = ""
        self.dpi           = 1200
        self.card_w        = CARD_WIDTH_MM
        self.card_h        = CARD_HEIGHT_MM
        self.gap           = GAP_MM
        self.show_crop     = True
        self.include_back  = True
        self.workers       = os.cpu_count() or 4
        self.pdf_format    = "PDF/X-4 (Trasparenze)"

        self.load_config()
        self._build_ui()

    # ------------------------------------------------------------------ UI ---

    def _build_ui(self):
        # Header
        header = BoxLayout(size_hint_y=None, height=dp(64),
                           padding=dp(12))
        from kivy.graphics import Color, Rectangle
        with header.canvas.before:
            Color(*COLORS["red"])
            self._header_rect = Rectangle(pos=header.pos, size=header.size)
        header.bind(pos=self._update_rect, size=self._update_rect)

        header.add_widget(Label(
            text="[b]🎴 Card Printer Pro[/b]",
            markup=True,
            font_size=dp(20),
            color=COLORS["white"],
        ))
        self.add_widget(header)

        # Scroll area
        scroll = ScrollView(do_scroll_x=False)
        content = BoxLayout(orientation="vertical", spacing=dp(12),
                            padding=[dp(12), dp(12)],
                            size_hint_y=None)
        content.bind(minimum_height=content.setter("height"))

        content.add_widget(self._section_files())
        content.add_widget(self._section_mode())
        content.add_widget(self._section_pdf_format())
        content.add_widget(self._section_settings())
        content.add_widget(self._section_info())
        content.add_widget(self._section_progress())
        content.add_widget(self._section_buttons())

        scroll.add_widget(content)
        self.add_widget(scroll)

        self._refresh_info()

    def _update_rect(self, instance, value):
        self._header_rect.pos  = instance.pos
        self._header_rect.size = instance.size

    # ---- sezioni ----

    def _section_files(self):
        box = SectionBox("📁 File e Cartelle", padding=[dp(4), dp(4)])

        # Cartella immagini
        row1 = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        self.lbl_folder = make_label("Nessuna cartella", font_size=12,
                                     color=COLORS["gray"])
        row1.add_widget(self.lbl_folder)
        row1.add_widget(make_button("📂 Immagini", self._browse_images,
                                    size_hint_x=None, width=dp(130)))
        box.add_widget(row1)

        # Logo retro
        row2 = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        self.lbl_logo = make_label("Nessun logo", font_size=12,
                                   color=COLORS["gray"])
        row2.add_widget(self.lbl_logo)
        self.btn_logo = make_button("🖼 Logo Retro", self._browse_logo,
                                    size_hint_x=None, width=dp(130))
        row2.add_widget(self.btn_logo)
        box.add_widget(row2)

        # Output PDF
        row3 = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        self.lbl_output = make_label("output.pdf", font_size=12,
                                     color=COLORS["gray"])
        row3.add_widget(self.lbl_output)
        row3.add_widget(make_button("💾 Output PDF", self._browse_output,
                                    size_hint_x=None, width=dp(130)))
        box.add_widget(row3)

        return box

    def _section_mode(self):
        box = SectionBox("🖨️ Modalità Stampa", padding=[dp(4), dp(4)])

        row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        self.chk_duplex = CheckBox(active=self.include_back,
                                   size_hint_x=None, width=dp(40))
        self.chk_duplex.bind(active=self._on_duplex_toggle)
        row.add_widget(self.chk_duplex)
        row.add_widget(make_label("Includi retro (modalità duplex)"))
        box.add_widget(row)

        self.lbl_mode_info = make_label("", font_size=12, color=COLORS["green"],
                                        size_hint_y=None, height=dp(24))
        box.add_widget(self.lbl_mode_info)
        self._update_mode_label()
        return box

    def _section_pdf_format(self):
        box = SectionBox("📄 Formato PDF", padding=[dp(4), dp(4)])

        self.spinner_format = Spinner(
            text=self.pdf_format,
            values=list(PDF_FORMATS.keys()),
            size_hint_y=None,
            height=dp(44),
            font_size=dp(13),
            background_color=COLORS["red"],
            color=COLORS["white"],
        )
        self.spinner_format.bind(text=self._on_format_change)
        box.add_widget(self.spinner_format)

        self.lbl_format_info = make_label("", font_size=12, color=COLORS["gray"],
                                          size_hint_y=None, height=dp(48))
        box.add_widget(self.lbl_format_info)
        self._update_format_label()
        return box

    def _section_settings(self):
        box = SectionBox("⚙️ Impostazioni Avanzate", padding=[dp(4), dp(4)])

        # DPI slider
        dpi_row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        dpi_row.add_widget(make_label("DPI:", size_hint_x=None, width=dp(40)))
        self.slider_dpi = Slider(min=600, max=2400, value=self.dpi,
                                  step=100)
        self.slider_dpi.bind(value=self._on_dpi_change)
        dpi_row.add_widget(self.slider_dpi)
        self.lbl_dpi = make_label(f"{self.dpi}", size_hint_x=None,
                                  width=dp(55), bold=True)
        dpi_row.add_widget(self.lbl_dpi)
        box.add_widget(dpi_row)

        # Dimensioni carta
        dims_row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        dims_row.add_widget(make_label("L(mm):", size_hint_x=None, width=dp(55)))
        self.ti_card_w = TextInput(text=str(self.card_w), multiline=False,
                                   input_filter="float",
                                   size_hint_x=None, width=dp(60),
                                   font_size=dp(14))
        self.ti_card_w.bind(text=lambda *_: self._refresh_info())
        dims_row.add_widget(self.ti_card_w)

        dims_row.add_widget(make_label("A(mm):", size_hint_x=None, width=dp(55)))
        self.ti_card_h = TextInput(text=str(self.card_h), multiline=False,
                                   input_filter="float",
                                   size_hint_x=None, width=dp(60),
                                   font_size=dp(14))
        self.ti_card_h.bind(text=lambda *_: self._refresh_info())
        dims_row.add_widget(self.ti_card_h)

        dims_row.add_widget(make_label("Gap:", size_hint_x=None, width=dp(38)))
        self.ti_gap = TextInput(text=str(self.gap), multiline=False,
                                input_filter="float",
                                size_hint_x=None, width=dp(50),
                                font_size=dp(14))
        self.ti_gap.bind(text=lambda *_: self._refresh_info())
        dims_row.add_widget(self.ti_gap)
        box.add_widget(dims_row)

        # Thread
        thread_row = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        thread_row.add_widget(make_label("Thread:", size_hint_x=None,
                                         width=dp(65)))
        self.spinner_workers = Spinner(
            text=str(self.workers),
            values=[str(i) for i in range(1, (os.cpu_count() or 4) + 1)],
            size_hint_x=None, width=dp(70),
            height=dp(40), size_hint_y=None,
            font_size=dp(14),
        )
        thread_row.add_widget(self.spinner_workers)
        thread_row.add_widget(make_label(f"(CPU: {os.cpu_count()} core)",
                                          font_size=12, color=COLORS["gray"]))
        box.add_widget(thread_row)

        # Segni di taglio
        crop_row = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(8))
        self.chk_crop = CheckBox(active=self.show_crop,
                                  size_hint_x=None, width=dp(40))
        crop_row.add_widget(self.chk_crop)
        crop_row.add_widget(make_label("Mostra segni di taglio"))
        box.add_widget(crop_row)

        return box

    def _section_info(self):
        box = SectionBox("ℹ️ Info Griglia", padding=[dp(4), dp(4)])
        self.lbl_info = make_label("", font_size=12, color=COLORS["text"],
                                   size_hint_y=None, height=dp(80))
        box.add_widget(self.lbl_info)
        return box

    def _section_progress(self):
        box = BoxLayout(orientation="vertical", spacing=dp(4),
                        size_hint_y=None, height=dp(64))
        self.progress_bar = ProgressBar(max=100, value=0,
                                         size_hint_y=None, height=dp(24))
        box.add_widget(self.progress_bar)
        self.lbl_progress = make_label("Pronto", font_size=12,
                                        color=COLORS["gray"],
                                        size_hint_y=None, height=dp(24))
        box.add_widget(self.lbl_progress)
        return box

    def _section_buttons(self):
        row = BoxLayout(size_hint_y=None, height=dp(52), spacing=dp(8))
        self.btn_generate = make_button("⚡ Genera PDF", self._generate,
                                         bg=COLORS["red"], font_size=15)
        row.add_widget(self.btn_generate)
        row.add_widget(make_button("💾 Salva cfg", self._save_config,
                                    bg=COLORS["gray"]))
        row.add_widget(make_button("ℹ️", self._show_about,
                                    bg=COLORS["gray"],
                                    size_hint_x=None, width=dp(52)))
        return row

    # ---------------------------------------------------------------- Logic ---

    def _read_params(self):
        try:
            self.card_w = float(self.ti_card_w.text or CARD_WIDTH_MM)
        except ValueError:
            self.card_w = CARD_WIDTH_MM
        try:
            self.card_h = float(self.ti_card_h.text or CARD_HEIGHT_MM)
        except ValueError:
            self.card_h = CARD_HEIGHT_MM
        try:
            self.gap = float(self.ti_gap.text or GAP_MM)
        except ValueError:
            self.gap = GAP_MM
        self.dpi          = int(self.slider_dpi.value)
        self.include_back = self.chk_duplex.active
        self.show_crop    = self.chk_crop.active
        self.workers      = int(self.spinner_workers.text)
        self.pdf_format   = self.spinner_format.text

    def _refresh_info(self, *_):
        self._read_params()
        positions      = compute_grid_positions(PAGE_W, PAGE_H,
                                                self.card_w, self.card_h,
                                                self.gap)
        cards_per_page = len(positions)
        w_px           = mm_to_px(self.card_w, self.dpi)
        h_px           = mm_to_px(self.card_h, self.dpi)
        mode           = "Duplex" if self.include_back else "Solo fronte"
        fmt            = PDF_FORMATS[self.pdf_format]["name"]

        self.lbl_info.text = (
            f"📏 Risoluzione carta: {w_px}×{h_px} px\n"
            f"📄 Carte per pagina: {cards_per_page}\n"
            f"🖨️ Modalità: {mode}   📋 Formato: {fmt}"
        )

    def _update_mode_label(self):
        if self.include_back:
            self.lbl_mode_info.text  = "✓ Duplex: fronte (carte) + retro (logo)"
            self.lbl_mode_info.color = COLORS["green"]
        else:
            self.lbl_mode_info.text  = "○ Solo fronte: senza retro"
            self.lbl_mode_info.color = COLORS["orange"]

    def _update_format_label(self):
        descs = {
            "PDF Standard":             "✓ Uso generale, compatibile con tutti",
            "PDF/A-1b (Archiviazione)": "✓ Archiviazione long-term, font incorporati",
            "PDF/X-1a (Stampa CMYK)":   "✓ Stampa professionale CMYK",
            "PDF/X-3 (Stampa ICC)":     "✓ Gestione colore ICC, RGB+CMYK",
            "PDF/X-4 (Trasparenze)":    "⭐ CONSIGLIATO – trasparenze e livelli",
        }
        self.lbl_format_info.text = descs.get(self.pdf_format, "")

    # ---- callbacks ----

    def _on_duplex_toggle(self, chk, val):
        self.include_back = val
        self.btn_logo.disabled = not val
        self._update_mode_label()
        self._refresh_info()

    def _on_format_change(self, spinner, text):
        self.pdf_format = text
        self._update_format_label()
        self._refresh_info()

    def _on_dpi_change(self, slider, value):
        self.dpi = int(value)
        self.lbl_dpi.text = str(self.dpi)
        self._refresh_info()

    # ---- file browsing ----

    def _browse_images(self, *_):
        FileChooserPopup(
            callback=self._set_image_folder,
            select_dir=True,
            title="Cartella Immagini",
        ).open()

    def _set_image_folder(self, path):
        self.image_folder = path
        self.lbl_folder.text = os.path.basename(path) or path
        self.lbl_folder.color = COLORS["text"]
        if not self.output_path:
            self.output_path = os.path.join(path, "carte_stampabili.pdf")
            self.lbl_output.text  = "carte_stampabili.pdf"
            self.lbl_output.color = COLORS["text"]

    def _browse_logo(self, *_):
        FileChooserPopup(
            callback=self._set_logo,
            select_dir=False,
            filters=["*.png", "*.jpg", "*.jpeg", "*.bmp"],
            title="Logo Retro",
        ).open()

    def _set_logo(self, path):
        self.logo_path       = path
        self.lbl_logo.text   = os.path.basename(path)
        self.lbl_logo.color  = COLORS["text"]

    def _browse_output(self, *_):
        # Su Android apriamo una cartella e impostiamo nome fisso
        FileChooserPopup(
            callback=self._set_output_folder,
            select_dir=True,
            title="Cartella Output PDF",
        ).open()

    def _set_output_folder(self, path):
        self.output_path     = os.path.join(path, "carte_stampabili.pdf")
        self.lbl_output.text = self.output_path
        self.lbl_output.color = COLORS["text"]

    # ---- generazione ----

    def _progress_cb(self, value, message):
        Clock.schedule_once(lambda dt: self._update_progress(value, message))

    def _update_progress(self, value, message):
        self.progress_bar.value = value
        self.lbl_progress.text  = message

    def _generate(self, *_):
        self._read_params()

        if not self.image_folder:
            self._alert("Errore", "Seleziona la cartella immagini!")
            return
        if self.include_back and not self.logo_path:
            self._alert("Errore", "Seleziona il logo retro o disabilita il duplex!")
            return
        if not self.output_path:
            self._alert("Errore", "Specifica il file di output!")
            return

        self.btn_generate.disabled = True
        t = threading.Thread(target=self._generate_worker, daemon=True)
        t.start()

    def _generate_worker(self):
        try:
            success, msg = make_pdf(
                self.image_folder,
                self.output_path,
                self.logo_path,
                self._progress_cb,
                self.dpi,
                self.card_w,
                self.card_h,
                self.gap,
                self.show_crop,
                self.workers,
                self.include_back,
                self.pdf_format,
            )
            if success:
                Clock.schedule_once(lambda dt: self._alert("✅ Successo!", msg))
            else:
                Clock.schedule_once(lambda dt: self._alert("❌ Errore", msg))
        except Exception as e:
            err = str(e)
            Clock.schedule_once(lambda dt: self._alert("❌ Errore", err))
        finally:
            Clock.schedule_once(lambda dt: setattr(self.btn_generate,
                                                    "disabled", False))

    # ---- config ----

    def save_config(self):
        self._save_config()

    def _save_config(self, *_):
        self._read_params()
        cfg = {
            "dpi":         self.dpi,
            "card_width":  self.card_w,
            "card_height": self.card_h,
            "gap":         self.gap,
            "show_crop":   self.show_crop,
            "include_back": self.include_back,
            "workers":     self.workers,
            "pdf_format":  self.pdf_format,
            "last_logo":   self.logo_path,
            "last_folder": self.image_folder,
        }
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump(cfg, f, indent=2)
            self._alert("💾 Salvato", "Impostazioni salvate!")
        except Exception as e:
            self._alert("Errore", f"Impossibile salvare: {e}")

    def load_config(self):
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE) as f:
                    c = json.load(f)
                self.dpi          = c.get("dpi",          1200)
                self.card_w       = c.get("card_width",   CARD_WIDTH_MM)
                self.card_h       = c.get("card_height",  CARD_HEIGHT_MM)
                self.gap          = c.get("gap",           GAP_MM)
                self.show_crop    = c.get("show_crop",    True)
                self.include_back = c.get("include_back", True)
                self.workers      = c.get("workers",      os.cpu_count() or 4)
                self.pdf_format   = c.get("pdf_format",   "PDF/X-4 (Trasparenze)")
                self.logo_path    = c.get("last_logo",    "")
                self.image_folder = c.get("last_folder",  "")
        except Exception:
            pass

    # ---- utils ----

    def _alert(self, title, message):
        content = BoxLayout(orientation="vertical", padding=dp(12),
                            spacing=dp(8))
        content.add_widget(make_label(message, font_size=13))
        btn = make_button("OK", lambda *_: popup.dismiss())
        content.add_widget(btn)
        popup = Popup(title=title, content=content,
                      size_hint=(0.85, 0.4),
                      title_color=COLORS["text"])
        popup.open()

    def _show_about(self, *_):
        self._alert("ℹ️ Card Printer Pro",
                    "Versione Android (Kivy)\n\n"
                    "• Stampa fronte o duplex\n"
                    "• Fino a 2400 DPI\n"
                    "• Multi-thread\n"
                    "• Segni di taglio\n"
                    "• Formati PDF professionali\n\n"
                    "Creato per la community Vanguard! 🃏")


# ===================== KIVY APP =====================

class CardPrinterApp(App):
    def build(self):
        Window.clearcolor = COLORS["bg"]
        if ANDROID:
            request_permissions([
                Permission.READ_EXTERNAL_STORAGE,
                Permission.WRITE_EXTERNAL_STORAGE,
            ])
        return CardPrinterRoot()

    def get_application_name(self):
        return "Card Printer Pro"


if __name__ == "__main__":
    CardPrinterApp().run()
