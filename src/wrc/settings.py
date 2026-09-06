from wrc.config import get_settings

_settings = get_settings()

BOT_NAME = "wrc"
SPIDER_MODULES = ["wrc.spiders"]
NEWSPIDER_MODULE = "wrc.spiders"

USER_AGENT = _settings.user_agent
ROBOTSTXT_OBEY = _settings.robotstxt_obey

# The source is stateless and hands out a throwaway session cookie on every response;
# keeping the cookie middleware would add per-request state for no benefit.
COOKIES_ENABLED = False

CONCURRENT_REQUESTS = _settings.concurrent_requests
CONCURRENT_REQUESTS_PER_DOMAIN = _settings.concurrent_requests_per_domain
DOWNLOAD_DELAY = _settings.request_delay
DOWNLOAD_TIMEOUT = _settings.download_timeout

# AutoThrottle adapts the delay to observed latency, so the crawl backs off when the
# server slows rather than holding a fixed rate into a struggling host.
AUTOTHROTTLE_ENABLED = _settings.autothrottle_enabled
AUTOTHROTTLE_START_DELAY = _settings.request_delay
AUTOTHROTTLE_MAX_DELAY = _settings.autothrottle_max_delay
AUTOTHROTTLE_TARGET_CONCURRENCY = _settings.autothrottle_target_concurrency

RETRY_ENABLED = True
RETRY_TIMES = _settings.retry_times
RETRY_HTTP_CODES = [429, 500, 502, 503, 504, 522, 524, 408]

LOG_LEVEL = _settings.log_level
TELNETCONSOLE_ENABLED = False
REQUEST_FINGERPRINTER_IMPLEMENTATION = "2.7"
TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"
FEED_EXPORT_ENCODING = "utf-8"
