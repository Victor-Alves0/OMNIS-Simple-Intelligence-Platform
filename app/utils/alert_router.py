import json
import urllib.request
import urllib.error

from tenacity import retry, stop_after_attempt, wait_exponential

from app.config.settings import SLACK_WEBHOOK_URL
from app.utils.omnis_logger import logger


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
def _post_payload(payload: dict) -> None:
    req = urllib.request.Request(
        SLACK_WEBHOOK_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        resp.read()


def send_alert(title: str, message: str, *, severity: str = "info") -> None:
    if not SLACK_WEBHOOK_URL:
        logger.debug("AlertRouter | Webhook not configured")
        return
    payload = {
        "text": f"[{severity.upper()}] {title}\n{message}"
    }
    try:
        _post_payload(payload)
        logger.info("AlertRouter | Alert sent to Slack")
    except urllib.error.URLError as exc:
        logger.warning("AlertRouter | Failed to send alert: %s", exc)
