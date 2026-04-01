import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from typing import Callable, Dict, List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


BASE_URL = "https://www.enbek.kz"
SEARCH_URL = f"{BASE_URL}/ru/search/resume"
ALMATY_REGION_ID = 75

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}

KEYWORD_GROUPS: Dict[str, List[str]] = {
    "sales_manager": [
        "менеджер по продажам",
        "специалист по продажам",
        "менеджер продаж",
        "sales manager",
        "менеджер по работе с клиентами",
        "аккаунт-менеджер",
        "менеджер по привлечению клиентов",
        "менеджер по развитию продаж",
        "менеджер b2b продаж",
        "менеджер b2c продаж",
    ],
    "call_center_operator": [
        "оператор call центра",
        "оператор call-центра",
        "оператор колл центра",
        "оператор колл-центра",
        "оператор контакт центра",
        "оператор контакт-центра",
        "специалист call центра",
        "специалист контакт центра",
        "телемаркетолог",
        "оператор на телефоне",
        "специалист по работе с клиентами",
        "менеджер call центра",
    ],
}

MAX_WORKERS = 4
BATCH_PAUSE_MIN = 0.6
BATCH_PAUSE_MAX = 1.2
REQUEST_TIMEOUT = (7, 25)
USE_LXML = True

_thread_local = threading.local()
_ws_re = re.compile(r"\s+")

ProgressCallback = Optional[Callable[[str], None]]


@dataclass
class ResumeCard:
    keyword_group: Optional[str] = None
    keyword_query: Optional[str] = None
    page_found: Optional[int] = None
    title: Optional[str] = None
    category: Optional[str] = None
    experience: Optional[str] = None
    location: Optional[str] = None
    salary: Optional[str] = None
    education: Optional[str] = None
    published_at: Optional[str] = None
    detail_url: Optional[str] = None


def create_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.7,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(
        pool_connections=MAX_WORKERS * 2,
        pool_maxsize=MAX_WORKERS * 2,
        max_retries=retry,
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(HEADERS)
    return session


def get_session() -> requests.Session:
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = create_session()
        _thread_local.session = session
    return session


def fetch_html(url: str, params: Optional[dict] = None) -> str:
    session = get_session()
    response = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.text


def clean_text(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    value = _ws_re.sub(" ", value).strip()
    return value or None


def normalize_for_compare(text: Optional[str]) -> str:
    if not text:
        return ""
    return _ws_re.sub(" ", text).strip().lower()


def looks_like_salary(text: str) -> bool:
    t = text.lower()
    return "тг" in t or "тенге" in t


def looks_like_experience(text: str) -> bool:
    t = text.lower()
    return "стажа" in t or "опыта" in t or "без опыта" in t


def looks_like_publication(text: str) -> bool:
    return "опубликовано" in text.lower()


def looks_like_education(text: str) -> bool:
    t = text.lower()
    markers = [
        "высшее",
        "техническое и профессиональное",
        "общее среднее",
        "послесреднее",
        "основное среднее",
        "послевузовское",
        "незаконченное высшее",
    ]
    return any(marker in t for marker in markers)


def looks_like_location(text: str) -> bool:
    t = text.lower()
    markers = ["г.", "область", "район", "город", "алматы"]
    return any(marker in t for marker in markers)


def get_parser() -> str:
    if USE_LXML:
        try:
            import lxml  # noqa: F401

            return "lxml"
        except Exception:
            pass
    return "html.parser"


def find_resume_cards(soup: BeautifulSoup) -> List[Tag]:
    cards: List[Tag] = []
    seen_urls = set()

    for link in soup.select('a[href*="/resume/"]'):
        href = link.get("href", "")
        if not href:
            continue

        full_url = urljoin(BASE_URL, href)
        if full_url in seen_urls:
            continue

        seen_urls.add(full_url)

        container: Tag = link
        chosen: Tag = link
        for _ in range(6):
            parent = container.parent
            if not parent or not isinstance(parent, Tag):
                break
            container = parent
            text = container.get_text(" ", strip=True)
            text_len = len(text)

            if 80 <= text_len <= 1200:
                chosen = container
                if "Опубликовано" in text or "/resume/" in str(container):
                    break
            if text_len > 1500:
                break

        cards.append(chosen)

    return cards


def extract_card_data(card: Tag, keyword_group: str, keyword_query: str, page_found: int) -> ResumeCard:
    lines: List[str] = []
    for item in card.stripped_strings:
        text = clean_text(item)
        if text:
            lines.append(text)

    unique_lines: List[str] = []
    seen = set()
    for line in lines:
        key = line.lower()
        if key not in seen:
            seen.add(key)
            unique_lines.append(line)

    result = ResumeCard(
        keyword_group=keyword_group,
        keyword_query=keyword_query,
        page_found=page_found,
    )

    resume_link = card.select_one('a[href*="/resume/"]')
    if resume_link:
        href = resume_link.get("href", "")
        if href:
            result.detail_url = urljoin(BASE_URL, href)

    unknown = []
    for line in unique_lines:
        if result.published_at is None and looks_like_publication(line):
            result.published_at = line
        elif result.salary is None and looks_like_salary(line):
            result.salary = line
        elif result.experience is None and looks_like_experience(line):
            result.experience = line
        elif result.education is None and looks_like_education(line):
            result.education = line
        elif result.location is None and looks_like_location(line):
            result.location = line
        else:
            unknown.append(line)

    if unknown:
        result.title = unknown[0]
    if len(unknown) > 1:
        result.category = unknown[1]

    return result


def parse_resume_search_page(html: str, keyword_group: str, keyword_query: str, page_found: int) -> List[ResumeCard]:
    soup = BeautifulSoup(html, get_parser())
    card_nodes = find_resume_cards(soup)

    results: List[ResumeCard] = []
    for node in card_nodes:
        item = extract_card_data(node, keyword_group, keyword_query, page_found)
        if item.title and (item.detail_url or item.salary or item.experience or item.location):
            results.append(item)

    return results


def search_resumes_once(keyword_group: str, keyword_query: str, page: int) -> List[ResumeCard]:
    params = {"prof": keyword_query, "region_id": ALMATY_REGION_ID, "page": page}
    html = fetch_html(SEARCH_URL, params=params)
    return parse_resume_search_page(html, keyword_group, keyword_query, page)


def item_key(item: ResumeCard) -> str:
    return item.detail_url or (
        f"{normalize_for_compare(item.title)}|"
        f"{normalize_for_compare(item.salary)}|"
        f"{normalize_for_compare(item.location)}"
    )


def deduplicate(items: List[ResumeCard]) -> List[ResumeCard]:
    deduped: List[ResumeCard] = []
    seen = set()
    for item in items:
        key = item_key(item)
        if key not in seen:
            seen.add(key)
            deduped.append(item)
    return deduped


def _emit_progress(progress_cb: ProgressCallback, message: str) -> None:
    if not progress_cb:
        return
    try:
        progress_cb(str(message))
    except Exception:
        pass


def crawl_keyword(
    executor: ThreadPoolExecutor,
    keyword_group: str,
    keyword: str,
    pages_per_query: int,
    progress_cb: ProgressCallback = None,
) -> List[ResumeCard]:
    collected: List[ResumeCard] = []
    page = 1
    empty_streak = 0
    _emit_progress(progress_cb, f"[{keyword_group}] \"{keyword}\": старт (до {pages_per_query} стр.)")

    while page <= pages_per_query:
        batch_pages = list(range(page, min(page + MAX_WORKERS - 1, pages_per_query) + 1))
        _emit_progress(progress_cb, f"[{keyword_group}] \"{keyword}\": загружаю стр. {batch_pages[0]}-{batch_pages[-1]}")
        futures = {executor.submit(search_resumes_once, keyword_group, keyword, p): p for p in batch_pages}

        batch_results = {}
        for future in as_completed(futures):
            p = futures[future]
            try:
                batch_results[p] = future.result()
            except requests.HTTPError:
                batch_results[p] = []
                _emit_progress(progress_cb, f"[{keyword_group}] \"{keyword}\": HTTP ошибка на стр. {p}")
            except Exception:
                batch_results[p] = []
                _emit_progress(progress_cb, f"[{keyword_group}] \"{keyword}\": ошибка на стр. {p}")

        for p in sorted(batch_results):
            page_items = batch_results[p]
            collected.extend(page_items)
            _emit_progress(progress_cb, f"[{keyword_group}] \"{keyword}\": стр. {p}, найдено {len(page_items)}")
            if not page_items:
                empty_streak += 1
            else:
                empty_streak = 0

        if empty_streak >= 2:
            _emit_progress(progress_cb, f"[{keyword_group}] \"{keyword}\": 2 пустые страницы подряд, остановка")
            break

        page = batch_pages[-1] + 1
        if page <= pages_per_query:
            time.sleep(random.uniform(BATCH_PAUSE_MIN, BATCH_PAUSE_MAX))

    _emit_progress(progress_cb, f"[{keyword_group}] \"{keyword}\": завершено, всего {len(collected)} карточек")
    return collected


def crawl_all(
    pages_per_query: int = 5,
    keyword_groups: Optional[Dict[str, List[str]]] = None,
    progress_cb: ProgressCallback = None,
) -> List[ResumeCard]:
    effective_groups = keyword_groups or KEYWORD_GROUPS
    total_queries = sum(len(v or []) for v in effective_groups.values())
    _emit_progress(
        progress_cb,
        f"Запуск парсинга: групп={len(effective_groups)}, запросов={total_queries}, страниц на запрос={pages_per_query}",
    )

    collected: List[ResumeCard] = []
    query_index = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        for keyword_group, keywords in effective_groups.items():
            group_keywords = list(keywords or [])
            if not group_keywords:
                _emit_progress(progress_cb, f"[{keyword_group}] нет ключевых слов, пропуск")
                continue

            _emit_progress(progress_cb, f"[{keyword_group}] ключевых слов: {len(group_keywords)}")
            for keyword in group_keywords:
                query_index += 1
                _emit_progress(progress_cb, f"Запрос {query_index}/{total_queries}: [{keyword_group}] \"{keyword}\"")
                items = crawl_keyword(
                    executor=executor,
                    keyword_group=keyword_group,
                    keyword=keyword,
                    pages_per_query=pages_per_query,
                    progress_cb=progress_cb,
                )
                collected.extend(items)
    deduped = deduplicate(collected)
    _emit_progress(
        progress_cb,
        f"Парсинг завершён: собрано {len(collected)} карточек, после дедупликации {len(deduped)}",
    )
    return deduped


def crawl_resumes_as_dicts(
    pages_per_query: int = 5,
    keyword_groups: Optional[Dict[str, List[str]]] = None,
    progress_cb: ProgressCallback = None,
) -> List[dict]:
    return [
        asdict(item)
        for item in crawl_all(
            pages_per_query=pages_per_query,
            keyword_groups=keyword_groups,
            progress_cb=progress_cb,
        )
    ]
