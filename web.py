#!/usr/bin/env python3
"""anagrind as a Django service. One file, no project scaffolding.

    python3 web.py            # http://127.0.0.1:8000

The same UI template drives this and the standalone build. Here it is served
with no dictionary embedded, so the page calls /api/solve and the answers come
from solver.py — the source of truth, including the combinatorial tier that
the browser build only approximates.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import os

import django
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.core.wsgi import get_wsgi_application
from django.urls import path

HERE = Path(__file__).parent

DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"

settings.configure(
    DEBUG=DEBUG,
    SECRET_KEY=os.environ.get("DJANGO_SECRET_KEY", "anagrind-dev-only"),
    ALLOWED_HOSTS=os.environ.get("ALLOWED_HOSTS", "*").split(","),
    ROOT_URLCONF=__name__,
    MIDDLEWARE=["django.middleware.common.CommonMiddleware"],
)
django.setup()

# Loaded once at import and shared across threads. Index is read-only after
# construction, so no locking is needed.
import vocab  # noqa: E402
from solver import (  # noqa: E402
    BAND_LABEL, diagnose, find_pattern, parse_pattern, solve, split_entry)

INDEX = vocab.load()
PAGE = (HERE / "ui.template.html").read_text().replace("__PAYLOAD__", "")


def home(request):
    return HttpResponse(PAGE, content_type="text/html; charset=utf-8")


def api_solve(request):
    fodder = request.GET.get("fodder", "")
    enumeration = request.GET.get("enum", "")
    include = bool(request.GET.get("all"))
    limit = min(int(request.GET.get("limit", 50)), 200)
    try:
        answers = solve(fodder, enumeration, INDEX,
                        limit=limit, include_unattested=include)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    return JsonResponse({
        "answers": [
            {"text": a.text, "parts": list(a.words), "band": a.band,
             "band_label": a.band_label, "tier": a.tier, "score": a.score}
            for a in answers
        ]
    }, json_dumps_params={"ensure_ascii": False})


def api_find(request):
    """Word finder: entries whose letters fit a pattern like 'r_c_n_l_'.

    The pattern carries its own enumeration in its separators, so unlike
    /api/solve there is no second parameter to disagree with it.
    """
    pattern = parse_pattern(request.GET.get("pattern", ""))
    try:
        limit = min(int(request.GET.get("limit", 25)), 500)
    except ValueError:
        return JsonResponse({"error": "limit must be a number"}, status=400)
    answers = find_pattern(pattern, INDEX, limit=limit)
    return JsonResponse({
        "enumeration": str(pattern.enumeration) if pattern.words else "",
        "answers": [
            {"text": a.text, "parts": list(a.words),
             "seps": list(split_entry(a.text)[1]),
             "band": a.band, "band_label": a.band_label,
             "tier": a.tier, "score": a.score}
            for a in answers
        ]
    }, json_dumps_params={"ensure_ascii": False})


def api_diagnose(request):
    """What would have worked. Only meaningful when /api/solve came back empty."""
    try:
        suggestions = diagnose(request.GET.get("fodder", ""),
                               request.GET.get("enum", ""), INDEX)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    return JsonResponse({
        "suggestions": [
            {"kind": s.kind, "detail": s.detail, "note": s.detail,
             "fodder": s.fodder, "enumeration": s.enumeration,
             "confident": s.confident,
             "answers": [{"text": a.text, "parts": list(a.words),
                          "band": a.band, "score": a.score} for a in s.answers]}
            for s in suggestions
        ]
    }, json_dumps_params={"ensure_ascii": False})


urlpatterns = [path("", home), path("api/solve", api_solve),
               path("api/find", api_find),
               path("api/diagnose", api_diagnose)]

# gunicorn web:application
application = get_wsgi_application()

if __name__ == "__main__":
    from django.core.management import execute_from_command_line

    execute_from_command_line([sys.argv[0], "runserver", *(sys.argv[1:] or ["8000"])])
