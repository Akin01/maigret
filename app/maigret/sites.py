import asyncio
import aiosqlite
import copy
import json
import os
import sys
from typing import Optional, List, Dict, Any, Tuple

from .utils import CaseConverter, URLMatcher, is_country_tag


class MaigretEngine:
    site: Dict[str, Any] = {}

    def __init__(self, name, data):
        self.name = name
        self.__dict__.update(data)

    @property
    def json(self):
        return self.__dict__


class MaigretSite:
    NOT_SERIALIZABLE_FIELDS = [
        "name", "engineData", "requestFuture", "detectedEngine", "engineObj", "stats", "urlRegexp",
    ]
    username_claimed = ""
    username_unclaimed = ""
    url_subpath = ""
    url_main = ""
    url = ""
    disabled = False
    similar_search = False
    ignore403 = False
    tags: List[str] = []
    type = "username"
    headers: Dict[str, str] = {}
    errors: Dict[str, str] = {}
    activation: Dict[str, Any] = {}
    regex_check = None
    url_probe = None
    check_type = ""
    request_head_only = ""
    get_params: Dict[str, Any] = {}
    presense_strs: List[str] = []
    absence_strs: List[str] = []
    stats: Dict[str, Any] = {}
    engine = None
    engine_data: Dict[str, Any] = {}
    engine_obj: Optional["MaigretEngine"] = None
    request_future = None
    alexa_rank = None
    source = None
    protocol = ''

    def __init__(self, name, information):
        self.name = name
        self.url_subpath = ""

        for k, v in information.items():
            if '_' in k:
                self.__dict__[k] = v
            else:
                self.__dict__[CaseConverter.camel_to_snake(k)] = v

        if not hasattr(self, 'protocol') or self.protocol is None:
            self.protocol = ''

        if (self.alexa_rank is None) or (self.alexa_rank == 0):
            self.alexa_rank = sys.maxsize

        self.update_detectors()

    def __str__(self):
        return f"{self.name} ({self.url_main})"

    def __is_equal_by_url_or_name(self, url_or_name_str: str):
        lower_url_or_name_str = url_or_name_str.lower()
        lower_url = self.url.lower()
        lower_name = self.name.lower()
        lower_url_main = self.url_main.lower()
        return (
            lower_name == lower_url_or_name_str
            or (lower_url_main and lower_url_main == lower_url_or_name_str)
            or (lower_url_main and lower_url_or_name_str in lower_url_main)
            or (lower_url_main and lower_url_or_name_str in lower_url_main)
            or (lower_url and lower_url_or_name_str in lower_url)
        )

    def __eq__(self, other):
        if isinstance(other, MaigretSite):
            attrs_to_compare = [
                'name', 'url_main', 'url_subpath', 'type', 'headers', 'errors',
                'activation', 'regex_check', 'url_probe', 'check_type',
                'request_head_only', 'get_params', 'presense_strs', 'absence_strs',
                'stats', 'engine', 'engine_data', 'alexa_rank', 'source', 'protocol',
            ]
            return all(getattr(self, attr) == getattr(other, attr) for attr in attrs_to_compare)
        elif isinstance(other, str):
            return self.__is_equal_by_url_or_name(other)
        return False

    def update_detectors(self):
        if hasattr(self, 'url') and self.url:
            url = self.url
            if hasattr(self, 'url_main') and self.url_main:
                url = url.replace("{urlMain}", self.url_main)
            if hasattr(self, 'url_subpath') and self.url_subpath:
                url = url.replace("{urlSubpath}", self.url_subpath)
            self.url_regexp = URLMatcher.make_profile_url_regexp(url, self.regex_check)

    def detect_username(self, url: str) -> Optional[str]:
        if hasattr(self, 'url_regexp') and self.url_regexp:
            match_groups = self.url_regexp.match(url)
            if match_groups:
                return match_groups.groups()[-1].rstrip("/")
        return None

    def extract_id_from_url(self, url: str) -> Optional[Tuple[str, str]]:
        if not hasattr(self, 'url_regexp') or not self.url_regexp:
            return None
        match_groups = self.url_regexp.match(url)
        if not match_groups:
            return None
        _id = match_groups.groups()[-1].rstrip("/")
        _type = self.type
        return _id, _type

    @property
    def pretty_name(self):
        if self.source:
            return f"{self.name} [{self.source}]"
        return self.name

    @property
    def errors_dict(self) -> dict:
        errors: Dict[str, str] = {}
        if self.engine_obj:
            errors.update(self.engine_obj.site.get('errors', {}))
        errors.update(self.errors)
        return errors

    def get_url_template(self) -> str:
        url = URLMatcher.extract_main_part(self.url)
        if url.startswith("{username}"):
            url = "SUBDOMAIN"
        elif url == "":
            url = f"{self.url} ({self.engine or 'no engine'})"
        else:
            parts = url.split("/")
            url = "/" + "/".join(parts[1:])
        return url

    def update_from_engine(self, engine: MaigretEngine):
        engine_data = engine.site
        for k, v in engine_data.items():
            field = CaseConverter.camel_to_snake(k)
            if isinstance(v, dict):
                self.__dict__.get(field, {}).update(v)
            elif isinstance(v, list):
                self.__dict__[field] = self.__dict__.get(field, []) + v
            else:
                self.__dict__[field] = v
        self.engine_obj = engine
        self.update_detectors()
        return self


class MaigretDatabase:
    def __init__(self):
        self._tags: list = []
        self._sites: list = []
        self._engines: list = []

    @property
    def sites(self):
        return self._sites

    @property
    def sites_dict(self):
        return {site.name: site for site in self._sites}

    def has_site(self, site: MaigretSite):
        for s in self._sites:
            if site == s:
                return True
        return False

    def __contains__(self, site):
        return self.has_site(site)

    def ranked_sites_dict(
        self,
        reverse=False,
        top=sys.maxsize,
        tags=[],
        names=[],
        disabled=True,
        id_type="username",
    ):
        normalized_names = list(map(str.lower, names))
        normalized_tags = list(map(str.lower, tags))

        is_name_ok = lambda x: x.name.lower() in normalized_names
        is_source_ok = lambda x: x.source and x.source.lower() in normalized_names
        is_engine_ok = lambda x: isinstance(x.engine, str) and x.engine.lower() in normalized_tags
        is_tags_ok = lambda x: set(x.tags).intersection(set(normalized_tags))
        is_protocol_in_tags = lambda x: x.protocol and x.protocol in normalized_tags
        is_disabled_needed = lambda x: not x.disabled or ("disabled" in tags or disabled)
        is_id_type_ok = lambda x: x.type == id_type

        filter_tags_engines_fun = lambda x: not tags or is_engine_ok(x) or is_tags_ok(x) or is_protocol_in_tags(x)
        filter_names_fun = lambda x: not names or is_name_ok(x) or is_source_ok(x)
        filter_fun = lambda x: filter_tags_engines_fun(x) and filter_names_fun(x) and is_disabled_needed(x) and is_id_type_ok(x)

        filtered_list = [s for s in self.sites if filter_fun(s)]
        sorted_list = sorted(filtered_list, key=lambda x: x.alexa_rank, reverse=reverse)[:top]
        return {site.name: site for site in sorted_list}

    @property
    def engines(self):
        return self._engines

    @property
    def engines_dict(self):
        return {engine.name: engine for engine in self._engines}

    async def load_from_path(self, db_path: str):
        db = await aiosqlite.connect(db_path)
        db.row_factory = aiosqlite.Row

        engines_cursor = await db.execute("SELECT name, data FROM engines")
        engine_rows = await engines_cursor.fetchall()
        for row in engine_rows:
            engine_data = json.loads(row['data'])
            self._engines.append(MaigretEngine(row['name'], engine_data))

        sites_cursor = await db.execute("""
            SELECT s.*, GROUP_CONCAT(t.name) as tags
            FROM sites s
            LEFT JOIN site_tags st ON s.id = st.site_id
            LEFT JOIN tags t ON st.tag_id = t.id
            GROUP BY s.id
        """)
        site_rows = await sites_cursor.fetchall()

        for row in site_rows:
            site_data = dict(row)

            for field in ['headers', 'errors', 'activation', 'get_params', 'presense_strs', 'absence_strs', 'engine_data']:
                if site_data.get(field):
                    site_data[field] = json.loads(site_data[field])

            tags = site_data.pop('tags')
            site_data['tags'] = tags.split(',') if tags else []

            maigret_site = MaigretSite(site_data['name'], site_data)

            engine_name_cursor = await db.execute("SELECT e.name FROM engines e JOIN sites s ON s.engine_id = e.id WHERE s.id = ?", (row['id'],))
            engine_name_row = await engine_name_cursor.fetchone()
            if engine_name_row and engine_name_row['name'] in self.engines_dict:
                maigret_site.update_from_engine(self.engines_dict[engine_name_row['name']])

            self._sites.append(maigret_site)

        tags_cursor = await db.execute("SELECT name FROM tags")
        tag_rows = await tags_cursor.fetchall()
        self._tags = [row['name'] for row in tag_rows]

        await db.close()
        return self

    def extract_ids_from_url(self, url: str) -> dict:
        results = {}
        for s in self._sites:
            result = s.extract_id_from_url(url)
            if not result:
                continue
            _id, _type = result
            results[_id] = _type
        return results