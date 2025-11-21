# file: courts/andhra.py  (patched version)
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import requests
from pathlib import Path
import cv2
import numpy as np
from PIL import Image
from io import BytesIO
from bs4 import BeautifulSoup
import pytesseract
import re
from urllib.parse import quote, urljoin, unquote
from datetime import datetime
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

router = APIRouter()
templates = Jinja2Templates(directory="templates")

# === Constants ===
ANDHRA_BASE_URL = "https://hcservices.ecourts.gov.in/ecourtindiaHC/cases/"
ANDHRA_FORM_URL = f"{ANDHRA_BASE_URL}s_order.php?state_cd=2&dist_cd=1&court_code=1&stateNm=Andhra%20Pradesh"
ANDHRA_CAPTCHA_URL = "https://hcservices.ecourts.gov.in/ecourtindiaHC/securimage/securimage_show.php?"

PDFS_ROOT = Path("All_Pdfs")
PDFS_ROOT.mkdir(exist_ok=True)
PDF_DIR = PDFS_ROOT / "andhra"
PDF_DIR.mkdir(exist_ok=True)

# persistent session to preserve cookies
session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; scraper/1.0)"})


# ----------------- Utilities -----------------

def safe_text(s: str) -> str:
    """Strip BOM and whitespace, return a clean string."""
    if s is None:
        return ""
    if not isinstance(s, str):
        s = str(s)
    # remove BOM and normalize
    return s.encode("utf-8").decode("utf-8-sig").replace("\ufeff", "").strip()


def get_captcha(session: requests.Session) -> str:
    """Download captcha image and try OCR (best-effort)."""
    try:
        r = session.get(ANDHRA_CAPTCHA_URL, verify=False, timeout=15)
        img_bytes = r.content
        arr = np.frombuffer(img_bytes, np.uint8)
        opencv_img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if opencv_img is None:
            return ""
        # light cleaning for OCR
        mask = cv2.inRange(opencv_img, (100, 100, 100), (255, 255, 255))
        opencv_img[mask > 0] = (255, 255, 255)
        pil = Image.fromarray(opencv_img)
        text = pytesseract.image_to_string(
            pil,
            config='--psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
        ).strip()
        print("CAPTCHA:", text)
        return text
    except Exception as e:
        print("CAPTCHA error:", e)
        return ""


@router.get("/andhra/act-types")
async def get_act_types(search: str = ""):
    url = f"{ANDHRA_BASE_URL}s_actwise_qry.php"
    payload = {
        "__csrf_magic": "",
        "action_code": "fillActType",
        "state_code": "2",
        "dist_code": "1",
        "court_code": "1",
        "search_act": search,
    }
    res = session.post(url, data=payload, verify=False, timeout=20)
    raw_text = res.text.strip()
    act_list = []
    if raw_text:
        for part in raw_text.split("#"):
            if "~" in part:
                value, label = part.split("~", 1)
                if value.strip() != "0":
                    act_list.append({"value": value.strip(), "label": label.strip()})
    return act_list

def fetch_andhra_judges():
    """Return list of judges for the form select (used by your template)."""
    url = f"{ANDHRA_BASE_URL}s_order_qry.php"
    payload = {"__csrf_magic": "", "action_code": "fillJudges", "state_code": "2", "dist_code": "1", "court_code": "1"}
    try:
        res = session.post(url, data=payload, verify=False, timeout=15)
        res.raise_for_status()
        raw = res.text.strip()
        judges = []
        if raw:
            for part in raw.split("#"):
                if "~" in part:
                    v, l = part.split("~", 1)
                    judges.append({"value": v.strip(), "label": l.strip()})
        return judges
    except Exception as e:
        print("Error fetching judges:", e)
        return []


def find_pdf_candidate_from_html(html: str, base_url: str):
    """Try common patterns to find a PDF link inside the HTML wrapper."""
    soup = BeautifulSoup(html, "html.parser")
    # 1) <embed src=...>
    embed = soup.find("embed")
    if embed and embed.get("src"):
        return urljoin(base_url, embed["src"])
    # 2) <iframe src=...>
    iframe = soup.find("iframe")
    if iframe and iframe.get("src"):
        return urljoin(base_url, iframe["src"])
    # 3) <a href=...> with .pdf or display_pdf
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if ".pdf" in href.lower() or "display_pdf.php" in href:
            return urljoin(base_url, href)
    # 4) JS redirect
    m = re.search(r"window\.location(?:\.href)?\s*=\s*['\"]([^'\"]+)['\"]", html, re.I)
    if m:
        return urljoin(base_url, m.group(1))
    # 5) meta refresh
    meta = soup.find("meta", attrs={"http-equiv": re.compile("refresh", re.I)})
    if meta:
        content = meta.get("content", "")
        mm = re.search(r"url=(.*)", content, re.I)
        if mm:
            return urljoin(base_url, mm.group(1).strip())
    # fallback: first http(s) .pdf URL in text
    m2 = re.search(r"https?://[^\s'\"<>]+\.pdf", html, re.I)
    if m2:
        return m2.group(0)
    return None


def download_streaming(url: str, save_path: Path, referer: str = None) -> bool:
    """Download a URL with stream=True and validate PDF magic bytes."""
    try:
        headers = {"User-Agent": session.headers.get("User-Agent")}
        if referer:
            headers["Referer"] = referer
        with session.get(url, headers=headers, verify=False, timeout=40, stream=True) as r:
            if r.status_code != 200:
                print("Download failed status:", r.status_code, url)
                return False
            tmp = save_path.with_suffix(save_path.suffix + ".part")
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(8192):
                    if chunk:
                        f.write(chunk)
            # Validate first bytes
            with open(tmp, "rb") as f:
                start = f.read(4)
            ctype = (r.headers.get("Content-Type") or "").lower()
            if start == b"%PDF" or "application/pdf" in ctype:
                tmp.replace(save_path)
                return True
            else:
                try:
                    tmp.unlink()
                except Exception:
                    pass
                print("Downloaded file is not PDF:", url, ctype)
                return False
    except Exception as e:
        print("Streaming download error:", e, url)
        return False


def download_andhra_display(display_url: str, save_path: Path) -> bool:
    """
    Try to download actual PDF for the Andhra display URL.
    Strategy:
      - GET display_url; if content-type is PDF => save
      - else parse returned HTML for embed/iframe/a to find PDF link => download that
      - fallback: stream display_url and check magic bytes
    """
    try:
        headers = {"User-Agent": session.headers.get("User-Agent"), "Referer": ANDHRA_FORM_URL}
        resp = session.get(display_url, headers=headers, verify=False, timeout=25, allow_redirects=True)
    except Exception as e:
        print("Error requesting display page:", display_url, e)
        return False

    ctype = (resp.headers.get("Content-Type") or "").lower()
    # direct PDF
    if resp.status_code == 200 and "application/pdf" in ctype:
        try:
            save_path.write_bytes(resp.content)
            return True
        except Exception as e:
            print("Error saving direct PDF:", e, display_url)
            return False
    # maybe content returned but not with PDF header -> check magic bytes
    if resp.status_code == 200 and resp.content[:4] == b"%PDF":
        try:
            save_path.write_bytes(resp.content)
            return True
        except Exception as e:
            print("Error saving pdf-by-magic:", e)
            return False

    # otherwise parse for embed/iframe/js/meta -> candidate
    html = resp.text or ""
    candidate = find_pdf_candidate_from_html(html, display_url)
    if candidate:
        # unquote in case of double-encoding; then try streaming download
        candidate = unquote(candidate)
        return download_streaming(candidate, save_path, referer=display_url)

    # final fallback: request display_url again as stream and check magic bytes
    try:
        with session.get(display_url, headers=headers, verify=False, timeout=30, stream=True) as r2:
            tmp = save_path.with_suffix(save_path.suffix + ".part")
            with open(tmp, "wb") as f:
                for chunk in r2.iter_content(8192):
                    if chunk:
                        f.write(chunk)
            with open(tmp, "rb") as f:
                start = f.read(4)
            if start == b"%PDF" or "application/pdf" in (r2.headers.get("Content-Type") or "").lower():
                tmp.replace(save_path)
                return True
            try:
                tmp.unlink()
            except Exception:
                pass
    except Exception as e:
        print("Final fallback error:", e, display_url)

    print("No usable PDF candidate for:", display_url)
    return False


def make_pdf_filename_from_case(case_no: str) -> str:
    """
    Build filename like TYPE_NUM_YEAR.pdf from a case_no like 'CRLP/11871/2025'
    If can't parse, sanitize whole string.
    """
    cn = safe_text(case_no)
    m = re.match(r"([^/\\]+)[/\\]([^/\\]+)[/\\]([0-9]{4})", cn)
    if m:
        type_part = re.sub(r"\W+", "_", m.group(1).upper())
        num_part = re.sub(r"\W+", "_", m.group(2))
        year_part = m.group(3)
        return f"{type_part}_{num_part}_{year_part}.pdf"
    s = re.sub(r"[^A-Za-z0-9_\-]+", "_", cn)
    if not s:
        s = "case_unknown"
    return f"{s}.pdf"


# ----------------- Routes -----------------

@router.get("/andhra", response_class=HTMLResponse)
async def andhra_form(request: Request):
    judge_list = fetch_andhra_judges()
    return templates.TemplateResponse("andhra_form.html", {"request": request, "judge_list": judge_list})


@router.post("/andhra/results", response_class=HTMLResponse)
async def fetch_andhra_results(request: Request):
    """
    Flow:
      - Read submitted form
      - Decide endpoint & payload
      - POST to remote site
      - Parse returned delimited records
      - For each record: build display_pdf.php URL, download PDF into folder
      - After all downloads complete, render results HTML and return/save it
    """
    # ensure initial cookies
    try:
        session.get(ANDHRA_FORM_URL, verify=False, timeout=10)
    except Exception:
        pass

    # captcha (best-effort OCR)
    captcha = get_captcha(session)
    if not captcha:
        return HTMLResponse("CAPTCHA failed (OCR)", status_code=500)

    # read form
    form = await request.form()
    clean = {}
    for k, v in form.items():
        if v is None:
            continue
        s = str(v).strip()
        if s == "" or s == "0":
            continue
        clean[k] = s

    # map fields
    search_type = clean.get("search_type", "date")
    citation_no = clean.get("citation_no", "")
    act_name = clean.get("act_name", "")
    act_type_code = clean.get("act_type_code", "")
    section = clean.get("section", "")
    party_name = clean.get("party_name", "")
    rgyear = clean.get("rgyear", "")
    # judge fields: try both names for compatibility
    judge_code = clean.get("judge_code", "") or clean.get("nnjudgecode1", "")
    judge_from_date = clean.get("judge_from_date", "")
    judge_to_date = clean.get("judge_to_date", "")
    from_date = clean.get("from_date", "")
    to_date = clean.get("to_date", "")

    # decide endpoint & payload
    post_url = None
    payload = {}
    if search_type == "citation" and citation_no:
        search_label = f"citation_{citation_no}"
        post_url = f"{ANDHRA_BASE_URL}s_citation_qry.php"
        payload = {"__csrf_magic": "", "action_code": "showRecords", "state_code": "2", "dist_code": "1",
                   "captcha": captcha, "court_code": "1", "citation_no": citation_no}
    elif search_type == "act" and (act_name or act_type_code):
        search_label = f"act_{(act_name or act_type_code)}"
        post_url = f"{ANDHRA_BASE_URL}s_actwise_qry.php"
        payload = {"__csrf_magic": "", "action_code": "showRecords", "state_code": "2", "dist_code": "1",
                   "search_act": act_name, "actcode": act_type_code, "f": "Pending",
                   "under_sec": section, "captcha": captcha, "court_code": "1"}
    elif search_type == "party" and party_name and rgyear:
        search_label = f"party_{party_name}_{rgyear}"
        post_url = f"{ANDHRA_BASE_URL}s_partyorder_qry.php"
        payload = {"__csrf_magic": "", "action_code": "showRecords", "state_code": "2", "dist_code": "1",
                   "captcha": captcha, "court_code": "1", "partyname": party_name, "rgyear": rgyear}
    elif search_type == "judge" and judge_code and judge_from_date and judge_to_date:
        search_label = f"judge_{judge_code}_{judge_from_date}_to_{judge_to_date}"
        post_url = f"{ANDHRA_BASE_URL}s_order_qry.php"
        payload = {"__csrf_magic": "", "action_code": "showRecords", "state_code": "2", "dist_code": "1",
                   "temp_date1": judge_from_date, "temp_date2": judge_to_date, "judge_code": judge_code,
                   "captcha": captcha, "court_code": "1", "reportableJudges": "All", "typeOfOrders": "0"}
    else:
        # date fallback
        search_label = f"date_{from_date}_to_{to_date}"
        post_url = f"{ANDHRA_BASE_URL}s_orderdate_qry.php"
        payload = {"__csrf_magic": "", "action_code": "showRecords", "state_code": "2", "dist_code": "1",
                   "from_date": from_date, "to_date": to_date, "captcha": captcha, "court_code": "1"}

    # Create search folder (always)
    safe_search_label = re.sub(r"[^A-Za-z0-9_\-]+", "_", search_label) or "search"
    today_folder = PDF_DIR / datetime.now().strftime("%Y-%m-%d")
    search_folder = today_folder / safe_search_label
    search_folder.mkdir(parents=True, exist_ok=True)

    # POST and parse response
    try:
        resp = session.post(post_url, data=payload, verify=False, timeout=40)
        raw_text = resp.text or ""
    except Exception as e:
        print("Error posting search:", e)
        raw_text = ""
    (search_folder / "raw_response.txt").write_text(raw_text, encoding="utf-8")

    # parse records (both separators)
    if "##" in raw_text:
        records = [r for r in raw_text.split("##") if r.strip()]
    else:
        records = [r for r in raw_text.split("~2##") if r.strip()]

    data = []
    for i, rec in enumerate(records, 1):
        parts = rec.split("~")
        if len(parts) < 8:
            continue

        case_no = safe_text(parts[0])
        order_date = safe_text(parts[1])
        filename_token = safe_text(parts[2])     # ✔ correct token from server
        order_type = safe_text(parts[3])
        court_code = safe_text(parts[4])         # ✔ correct court code
        cino = safe_text(parts[7])               # ✔ correct CINO

        # Skip invalid tokens
        if not filename_token or filename_token == "N":
            print("❌ Invalid filename token for:", case_no)
            continue

        # Fix double-encoded tokens
        try:
            token = unquote(unquote(filename_token))
        except:
            token = filename_token

        filename_param = quote(token, safe="")

        # Build EXACT working URL
        display_page_url = (
            f"{ANDHRA_BASE_URL}display_pdf.php?"
            f"filename={filename_param}"
            f"&caseno={quote(case_no)}"
            f"&cCode={quote(court_code)}"
            f"&cino={quote(cino)}"
            f"&state_code=2&appFlag="
        )

        # prepare file path
        pdf_filename = make_pdf_filename_from_case(case_no)
        date_folder = search_folder / (order_date or "unknown_date")
        date_folder.mkdir(parents=True, exist_ok=True)
        save_path = date_folder / pdf_filename

        downloaded = False
        if not save_path.exists():
            downloaded = download_andhra_display(display_page_url, save_path)
        else:
            downloaded = True

        rel_link = f"./{order_date}/{pdf_filename}" if downloaded else ""
        data.append({
            "index": i,
            "case_no": case_no,
            "order_date": order_date,
            "order_type": order_type,
            "pdf_link": rel_link,
            "downloaded": downloaded
        })


    # After all downloads finished, render the results HTML and save it
    rendered_html = templates.get_template("andhra_results.html").render(request=request, data=data)
    html_path = search_folder / "results.html"
    html_path.write_text(rendered_html, encoding="utf-8")

    print(f"✅ Saved results HTML at: {html_path}")
    print(f"✅ PDFs stored in: {search_folder}")

    return HTMLResponse(rendered_html)
