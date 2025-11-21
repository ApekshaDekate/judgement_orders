import requests
from bs4 import BeautifulSoup
from PIL import Image
from io import BytesIO
import cv2
import numpy as np
import pytesseract
from fastapi import APIRouter

router = APIRouter()

# ----------------------------------------------------------
# Step 1️⃣: Captcha breaker function
# ----------------------------------------------------------
def solve_captcha_from_bytes(img_bytes):
    """
    Takes captcha image bytes, processes it, and returns the predicted text.
    """
    # Convert bytes to image
    pil_image = Image.open(BytesIO(img_bytes)).convert('RGB')
    img = np.array(pil_image)

    # Convert to grayscale
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)

    # Resize for better clarity
    gray = cv2.resize(gray, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)

    # Apply blur to remove noise
    gray = cv2.medianBlur(gray, 3)

    # Apply adaptive thresholding to highlight text
    thresh = cv2.adaptiveThreshold(
        gray, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        11, 2
    )

    # Apply morphological cleaning
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    processed = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)

    # OCR using pytesseract
    config = '-c tessedit_char_whitelist=0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ --psm 7'
    text = pytesseract.image_to_string(processed, config=config)

    # Keep only alphanumeric
    text = ''.join(ch for ch in text if ch.isalnum())
    return text.strip()


# ----------------------------------------------------------
# Step 2️⃣: Main request flow
# ----------------------------------------------------------
DATA_URL = "https://highcourt.cg.gov.in/hcbspjudgement/oj_search.php"

# Create a session (keeps cookies for captcha validation)
session = requests.Session()

# Step 3️⃣: Load the search page first (GET request)
resp = session.get(DATA_URL)
soup = BeautifulSoup(resp.text, "html.parser")

# Step 4️⃣: Find captcha <img> tag
captcha_img = soup.find("img", {"id": "captcha"})
captcha_src = captcha_img["src"]   # e.g., "captcha.php"

# Step 5️⃣: Build the full URL
captcha_url = f"https://highcourt.cg.gov.in/hcbspjudgement/{captcha_src}"

# Step 6️⃣: Download the captcha image
img_response = session.get(captcha_url)
img_bytes = img_response.content

# Step 7️⃣: Solve captcha
captcha_text = solve_captcha_from_bytes(img_bytes)
print("Predicted Captcha Text:", captcha_text)

# Step 8️⃣: Prepare form data (payload)
data = {
    "judge_name": -1,
    "case_type": 36,
    "captcha": captcha_text,
    "button1": "Submit"
}

# Step 9️⃣: Send POST request with solved captcha
response = session.post(DATA_URL, data=data)

# Step 🔟: Save response HTML for verification
with open("tmp.html", "w", encoding="utf-8") as f:
    f.write(response.text)

print("Search completed and saved as tmp.html ✅")
