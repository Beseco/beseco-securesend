"""
core/pdf.py — PDF-Erstellung & Verschlüsselung

Keine Flask-Abhängigkeit, keine globalen Variablen.
"""

from __future__ import annotations

import io
import re

try:
    from fpdf import FPDF as _FPDF
    from pypdf import PdfReader as _PdfReader, PdfWriter as _PdfWriter
    _PDF_LIBS_AVAILABLE = True
except ImportError:
    _PDF_LIBS_AVAILABLE = False


_UNICODE_REPLACE = {
    '\u2013': '-',    # en dash  –
    '\u2014': '--',   # em dash  —
    '\u2015': '--',   # horizontal bar
    '\u2018': "'",    # left single quotation mark
    '\u2019': "'",    # right single quotation mark
    '\u201a': ',',    # single low-9 quotation mark
    '\u201b': "'",    # single high-reversed-9 quotation mark
    '\u201c': '"',    # left double quotation mark
    '\u201d': '"',    # right double quotation mark
    '\u201e': '"',    # double low-9 quotation mark
    '\u2026': '...',  # horizontal ellipsis
    '\u2022': '-',    # bullet
    '\u2023': '>',    # triangular bullet
    '\u2039': '<',    # single left angle quotation
    '\u203a': '>',    # single right angle quotation
    '\u00ab': '"',    # left-pointing double angle quotation
    '\u00bb': '"',    # right-pointing double angle quotation
    '\u2032': "'",    # prime
    '\u2033': '"',    # double prime
    '\u00b7': '.',    # middle dot
    '\u2212': '-',    # minus sign
    '\u00d7': 'x',    # multiplication sign
    '\u00f7': '/',    # division sign
    '\u2192': '->',   # rightwards arrow
    '\u2190': '<-',   # leftwards arrow
    '\u2194': '<->',  # left right arrow
    '\u21d2': '=>',   # rightwards double arrow
    '\u2713': 'OK',   # check mark
    '\u2714': 'OK',   # heavy check mark
    '\u2717': 'X',    # ballot x
    '\u2718': 'X',    # heavy ballot x
}


def _to_latin1(text: str) -> str:
    """Ersetzt bekannte Unicode-Sonderzeichen durch Latin-1-kompatible Äquivalente."""
    for ch, repl in _UNICODE_REPLACE.items():
        text = text.replace(ch, repl)
    return text.encode('latin-1', errors='replace').decode('latin-1')


def _md_plain(text: str) -> str:
    """Entfernt Markdown-Inline-Syntax für Plain-Text-Ausgabe (z.B. im PDF)."""
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
    text = re.sub(r'__(.*?)__',     r'\1', text)
    text = re.sub(r'\*(.*?)\*',     r'\1', text)
    text = re.sub(r'_(.*?)_',       r'\1', text)
    text = re.sub(r'`(.*?)`',       r'\1', text)
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
    return _to_latin1(text)


def md_to_pdf_bytes(md_text: str, title: str = "Sichere Nachricht",
                    sender_name: str = "", sender_email: str = "") -> bytes:
    """
    Konvertiert Markdown-Text zu einem professionell gestalteten PDF (fpdf2).
    Unterstützte Elemente: H1/H2/H3, Bullet-/Nummerierte Listen,
    Blockquotes, Trennlinien, Codeblöcke, normaler Fließtext.

    sender_name / sender_email werden in der Fußzeile angezeigt.
    """
    if not _PDF_LIBS_AVAILABLE:
        raise RuntimeError("fpdf2/pypdf ist nicht installiert. Bitte 'pip install fpdf2 pypdf' ausführen.")

    _sender_name  = sender_name
    _sender_email = sender_email

    class PDF(_FPDF):
        def header(self):
            self.set_fill_color(26, 86, 219)
            self.rect(0, 0, 210, 24, 'F')
            self.set_y(5)
            self.set_font('Helvetica', 'B', 13)
            self.set_text_color(255, 255, 255)
            self.cell(0, 7, 'Beseco IT Systems  Sichere Nachricht', align='L',
                      new_x='LMARGIN', new_y='NEXT')
            self.set_font('Helvetica', '', 9)
            self.set_text_color(180, 210, 255)
            self.cell(0, 5, _to_latin1(title), align='L')
            self.ln(10)

        def footer(self):
            self.set_y(-14)
            self.set_font('Helvetica', 'I', 8)
            self.set_text_color(156, 163, 175)
            txt = _to_latin1(f'{_sender_name}  {_sender_email}  Seite {self.page_no()}')
            self.cell(0, 8, txt, align='C')

    pdf = PDF()
    pdf.set_margins(20, 35, 20)
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    in_code_block = False

    for raw_line in md_text.split('\n'):
        line = raw_line.rstrip()

        if line.strip().startswith('```'):
            in_code_block = not in_code_block
            if in_code_block:
                pdf.set_fill_color(30, 41, 59)
                pdf.set_font('Courier', '', 10)
                pdf.set_text_color(226, 232, 240)
            else:
                pdf.ln(2)
                pdf.set_text_color(55, 65, 81)
            continue
        if in_code_block:
            pdf.set_x(22)
            pdf.multi_cell(166, 5, _to_latin1(line), fill=True, new_x='LMARGIN', new_y='NEXT')
            continue

        if line.startswith('# '):
            pdf.set_font('Helvetica', 'B', 17)
            pdf.set_text_color(17, 24, 39)
            pdf.multi_cell(0, 8, _md_plain(line[2:]))
            pdf.ln(2)
        elif line.startswith('## '):
            pdf.set_font('Helvetica', 'B', 13)
            pdf.set_text_color(26, 86, 219)
            pdf.multi_cell(0, 7, _md_plain(line[3:]))
            pdf.set_draw_color(229, 231, 235)
            pdf.line(20, pdf.get_y(), 190, pdf.get_y())
            pdf.ln(3)
        elif line.startswith('### '):
            pdf.set_font('Helvetica', 'B', 11)
            pdf.set_text_color(55, 65, 81)
            pdf.multi_cell(0, 6, _md_plain(line[4:]))
            pdf.ln(1)
        elif line.startswith('> '):
            y = pdf.get_y()
            pdf.set_fill_color(26, 86, 219)
            pdf.rect(20, y, 2.5, 6.5, 'F')
            pdf.set_x(25)
            pdf.set_font('Helvetica', 'I', 10)
            pdf.set_text_color(55, 65, 81)
            pdf.set_fill_color(239, 246, 255)
            pdf.multi_cell(165, 6, _md_plain(line[2:]))
            pdf.ln(1)
        elif re.match(r'^[-*+] ', line):
            pdf.set_font('Helvetica', '', 10.5)
            pdf.set_text_color(55, 65, 81)
            pdf.set_x(25)
            pdf.cell(5, 5.5, chr(149))
            pdf.set_x(30)
            pdf.multi_cell(160, 5.5, _md_plain(line[2:]), new_x='LMARGIN', new_y='NEXT')
        elif re.match(r'^\d+\. ', line):
            m = re.match(r'^(\d+)\. (.*)', line)
            if m:
                pdf.set_font('Helvetica', '', 10.5)
                pdf.set_text_color(55, 65, 81)
                pdf.set_x(25)
                pdf.cell(6, 5.5, f'{m.group(1)}.')
                pdf.set_x(31)
                pdf.multi_cell(159, 5.5, _md_plain(m.group(2)), new_x='LMARGIN', new_y='NEXT')
        elif re.match(r'^[-*_]{3,}$', line.strip()):
            pdf.set_draw_color(229, 231, 235)
            pdf.line(20, pdf.get_y() + 2, 190, pdf.get_y() + 2)
            pdf.ln(5)
        elif line.strip() == '':
            pdf.ln(3)
        else:
            pdf.set_font('Helvetica', '', 10.5)
            pdf.set_text_color(55, 65, 81)
            pdf.multi_cell(0, 5.5, _md_plain(line))
            pdf.ln(0.5)

    return bytes(pdf.output())


def encrypt_pdf_bytes(pdf_bytes: bytes, password: str) -> bytes:
    """Verschlüsselt PDF-Bytes mit AES-256 (pypdf)."""
    if not _PDF_LIBS_AVAILABLE:
        raise RuntimeError("pypdf ist nicht installiert. Bitte 'pip install pypdf' ausführen.")
    reader = _PdfReader(io.BytesIO(pdf_bytes))
    writer = _PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.encrypt(password)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
