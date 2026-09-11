from pathlib import Path

from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from app.web import filters

WEB_DIR = Path(__file__).parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"

_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)), undefined=StrictUndefined, autoescape=True
)
templates = Jinja2Templates(env=_env)
templates.env.filters["humanize"] = filters.humanize
templates.env.filters["ratio_pct"] = filters.ratio_pct
templates.env.filters["pct"] = filters.pct
templates.env.filters["signed"] = filters.signed
templates.env.filters["dt"] = filters.dt
templates.env.filters["day"] = filters.day
templates.env.filters["ago"] = filters.ago
templates.env.filters["dash"] = filters.dash
templates.env.filters["health_class"] = filters.health_class
templates.env.filters["anomaly_class"] = filters.anomaly_class
templates.env.globals["tme_post_url"] = filters.tme_post_url
