"""Constants for the TGC-1 (ТГК-1) personal cabinet integration."""

DOMAIN = "tgc1"

BASE_URL = "https://lk.tgc1.ru/api"
LOGIN_URL = f"{BASE_URL}/security/auth/login/fl"
DEVICE_URL = f"{BASE_URL}/fl/device"
SALDO_URL = f"{BASE_URL}/fl/debt/saldo"
CREATE_READING_URL = f"{BASE_URL}/fl/device/create-reading"

# Документы (квитанции)
DOCUMENTS_URL = f"{BASE_URL}/fl/document"
DOCUMENT_FILE_URL = f"{BASE_URL}/fl/document/file/{{id}}"

SERVICE_SUBMIT_READING = "submit_reading"
SERVICE_DOWNLOAD_DOCUMENT = "download_document"

# Site itself only warns at +100; we block earlier by default (can be overridden).
DEFAULT_MAX_INCREASE = 30.0

# Readings change about once a month, so there is no point in polling often.
UPDATE_INTERVAL_HOURS = 3

# Папка для сохранения квитанций
MEDIA_DIR = "/media/tgc1"

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json",
}