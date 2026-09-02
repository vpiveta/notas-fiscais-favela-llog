import hmac
import io
import os
import re
import secrets
import unicodedata
import zipfile
from datetime import datetime
from functools import wraps
from uuid import uuid4
from zoneinfo import ZoneInfo

import requests
from flask import Flask, abort, flash, redirect, render_template, request, session, url_for, Response
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.utils import secure_filename


MAX_FILE_SIZE = 25 * 1024 * 1024
BUCKET = "notas-fiscais"
SAO_PAULO = ZoneInfo("America/Sao_Paulo")

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.getenv("FLASK_SECRET_KEY", secrets.token_hex(32)),
    MAX_CONTENT_LENGTH=MAX_FILE_SIZE,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=os.getenv("RENDER", "").lower() == "true",
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=3600,
)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)


def env(name):
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Variável obrigatória ausente: {name}")
    return value


def supabase_headers(extra=None):
    key = env("SUPABASE_SECRET_KEY")
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    if extra:
        headers.update(extra)
    return headers


def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


app.jinja_env.globals["csrf_token"] = csrf_token


@app.template_filter("br_datetime")
def br_datetime(value):
    if not value:
        return "—"
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("UTC"))
    return parsed.astimezone(SAO_PAULO).strftime("%d/%m/%Y · %H:%M")


def verify_csrf():
    sent = request.form.get("csrf_token", "")
    saved = session.get("csrf_token", "")
    if not sent or not saved or not hmac.compare_digest(sent, saved):
        abort(400, "Sessão expirada. Atualize a página e tente novamente.")


def digits(value):
    return re.sub(r"\D", "", value or "")


def valid_cpf(value):
    cpf = digits(value)
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False
    for size in (9, 10):
        total = sum(int(cpf[i]) * (size + 1 - i) for i in range(size))
        check = (total * 10) % 11
        check = 0 if check == 10 else check
        if check != int(cpf[size]):
            return False
    return True


def valid_month(value):
    return bool(re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", value or ""))


def safe_stem(name):
    normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-zA-Z0-9]+", "-", normalized).strip("-").lower()[:60] or "motorista"


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin_login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def fetch_invoices(month="", fortnight="", search="", limit="500"):
    params = {
        "select": "id,full_name,cpf,invoice_month,fortnight,original_filename,storage_path,file_size_bytes,created_at",
        "order": "created_at.desc",
        "limit": limit,
    }
    if valid_month(month):
        params["invoice_month"] = f"eq.{month}"
    if fortnight in {"1", "2"}:
        params["fortnight"] = f"eq.{fortnight}"
    if search:
        clean = search.replace(",", "").replace(".", "")[:80]
        params["or"] = f"(full_name.ilike.*{clean}*,cpf.ilike.*{digits(clean)}*)"
    response = requests.get(
        f"{env('SUPABASE_URL')}/rest/v1/invoices",
        headers=supabase_headers(), params=params, timeout=30,
    )
    if not response.ok:
        app.logger.error("Falha na listagem: %s", response.text)
        abort(502, "Não foi possível consultar as notas.")
    return response.json()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def index():
    current_month = datetime.now(SAO_PAULO).strftime("%Y-%m")
    return render_template("index.html", current_month=current_month)


@app.post("/enviar")
def upload_invoice():
    verify_csrf()
    full_name = " ".join(request.form.get("full_name", "").split())
    cpf = digits(request.form.get("cpf"))
    invoice_month = request.form.get("invoice_month", "").strip()
    fortnight = request.form.get("fortnight", "")
    file = request.files.get("pdf")

    errors = []
    if len(full_name) < 5 or len(full_name) > 160:
        errors.append("Informe o nome completo.")
    if not valid_cpf(cpf):
        errors.append("Informe um CPF válido.")
    if not valid_month(invoice_month):
        errors.append("Selecione o mês da nota fiscal.")
    if fortnight not in {"1", "2"}:
        errors.append("Selecione a quinzena.")
    if not file or not file.filename:
        errors.append("Selecione a nota fiscal em PDF.")

    content = b""
    if file and file.filename:
        content = file.read(MAX_FILE_SIZE + 1)
        if len(content) > MAX_FILE_SIZE:
            errors.append("O PDF deve ter no máximo 25 MB.")
        if not content.startswith(b"%PDF-"):
            errors.append("O arquivo enviado não é um PDF válido.")

    if errors:
        for error in errors:
            flash(error, "error")
        return redirect(url_for("index"))

    year, month = invoice_month.split("-")
    object_path = f"{year}/{month}/q{fortnight}/{safe_stem(full_name)}-{uuid4().hex}.pdf"
    storage_url = f"{env('SUPABASE_URL')}/storage/v1/object/{BUCKET}/{object_path}"
    uploaded = requests.post(
        storage_url,
        headers=supabase_headers({"Content-Type": "application/pdf", "x-upsert": "false"}),
        data=content,
        timeout=60,
    )
    if not uploaded.ok:
        app.logger.error("Falha no Storage: %s", uploaded.text)
        flash("Não foi possível salvar o PDF. Tente novamente.", "error")
        return redirect(url_for("index"))

    original = secure_filename(file.filename)[:255] or "nota-fiscal.pdf"
    record = {
        "full_name": full_name,
        "cpf": cpf,
        "invoice_month": invoice_month,
        "fortnight": int(fortnight),
        "original_filename": original,
        "storage_path": object_path,
        "file_size_bytes": len(content),
        "mime_type": "application/pdf",
    }
    saved = requests.post(
        f"{env('SUPABASE_URL')}/rest/v1/invoices",
        headers=supabase_headers({"Content-Type": "application/json", "Prefer": "return=minimal"}),
        json=record,
        timeout=30,
    )
    if not saved.ok:
        requests.delete(storage_url, headers=supabase_headers(), timeout=30)
        app.logger.error("Falha no banco: %s", saved.text)
        flash("Não foi possível registrar a nota. Tente novamente.", "error")
        return redirect(url_for("index"))

    session["upload_success"] = True
    return redirect(url_for("success"))


@app.get("/sucesso")
def success():
    if not session.pop("upload_success", False):
        return redirect(url_for("index"))
    return render_template("success.html")


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        verify_csrf()
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        expected_user = os.getenv("ADMIN_USERNAME", "piveta")
        expected_pass = env("ADMIN_PASSWORD")
        if hmac.compare_digest(username, expected_user) and hmac.compare_digest(password, expected_pass):
            session.clear()
            session["admin"] = True
            session.permanent = True
            return redirect(url_for("admin_dashboard"))
        flash("Usuário ou senha inválidos.", "error")
    return render_template("login.html")


@app.post("/admin/sair")
def admin_logout():
    verify_csrf()
    session.clear()
    return redirect(url_for("admin_login"))


@app.get("/admin")
@admin_required
def admin_dashboard():
    month = request.args.get("month", "").strip()
    fortnight = request.args.get("fortnight", "")
    search = request.args.get("search", "").strip()
    invoices = fetch_invoices(month=month, fortnight=fortnight, search=search)
    return render_template("admin.html", invoices=invoices, month=month, fortnight=fortnight, search=search)


@app.get("/admin/download/<uuid:invoice_id>")
@admin_required
def download_invoice(invoice_id):
    response = requests.get(
        f"{env('SUPABASE_URL')}/rest/v1/invoices",
        headers=supabase_headers(),
        params={"select": "original_filename,storage_path", "id": f"eq.{invoice_id}", "limit": "1"},
        timeout=30,
    )
    if not response.ok or not response.json():
        abort(404)
    invoice = response.json()[0]
    pdf = requests.get(
        f"{env('SUPABASE_URL')}/storage/v1/object/{BUCKET}/{invoice['storage_path']}",
        headers=supabase_headers(), timeout=60,
    )
    if not pdf.ok:
        abort(404)
    filename = secure_filename(invoice["original_filename"]) or "nota-fiscal.pdf"
    return Response(
        pdf.content,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
    )


@app.get("/admin/baixar-quinzena")
@admin_required
def download_fortnight():
    month = request.args.get("month", "").strip()
    fortnight = request.args.get("fortnight", "")
    if not valid_month(month) or fortnight not in {"1", "2"}:
        flash("Selecione o mês e a quinzena para baixar todas as notas.", "error")
        return redirect(url_for("admin_dashboard", month=month, fortnight=fortnight))

    invoices = fetch_invoices(month=month, fortnight=fortnight, limit="1000")
    if not invoices:
        flash("Nenhuma nota encontrada para o mês e quinzena selecionados.", "error")
        return redirect(url_for("admin_dashboard", month=month, fortnight=fortnight))

    output = io.BytesIO()
    used_names = set()
    added = 0
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in invoices:
            pdf = requests.get(
                f"{env('SUPABASE_URL')}/storage/v1/object/{BUCKET}/{item['storage_path']}",
                headers=supabase_headers(), timeout=60,
            )
            if not pdf.ok:
                app.logger.error("Falha ao baixar %s para ZIP: %s", item.get("storage_path"), pdf.text)
                continue
            base = f"{safe_stem(item['full_name'])}-{secure_filename(item['original_filename']) or 'nota-fiscal.pdf'}"
            filename = base
            counter = 2
            while filename.lower() in used_names:
                stem, ext = os.path.splitext(base)
                filename = f"{stem}-{counter}{ext}"
                counter += 1
            used_names.add(filename.lower())
            archive.writestr(filename, pdf.content)
            added += 1

    if added == 0:
        abort(502, "As notas foram encontradas, mas os PDFs não puderam ser baixados.")

    output.seek(0)
    zip_name = f"notas-fiscais-{month}-q{fortnight}.zip"
    return Response(
        output.getvalue(),
        mimetype="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_name}"', "Cache-Control": "no-store"},
    )


@app.errorhandler(413)
def too_large(_error):
    flash("O arquivo ultrapassa o limite de 25 MB.", "error")
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
