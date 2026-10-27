"""
i18n.py —— 中文 / English 双语切换

怎么用：
  · 模板里：{{ _("中文原文") }}
    带参数：{{ _("今日剩余 {n} 次", n=left) }}
  · 页面里的 JavaScript：__("中文原文")，带参数：__("今日剩余 {n} 次", {n: left})
    （模板的 <head> 里要有 {% include "_i18n.html" %}，它会加载 /i18n/<语言>.js）
  · 英文对照写在 translations_en.py 的 EN 字典里，key 就是中文原文。
    没写对照的句子，英文版会原样显示中文，不会报错。
  · 检查有没有漏翻：python3 tools/i18n_check.py

语言怎么决定：网址里的 ?lang=zh / ?lang=en  >  上次选过的（存在 cookie）  >  浏览器语言（中文→中文，其他→英文）
"""
import hashlib
import json
from urllib.parse import urlencode

from flask import Response, g, request
from markupsafe import Markup, escape

from translations_en import EN

LANGS = ("zh", "en")
COOKIE = "lang"
COOKIE_AGE = 365 * 24 * 3600

# 前端用的字典，按内容算个版本号，内容一变浏览器就会重新下载
_JS_DICT = {"zh": {}, "en": EN}
_VERSION = hashlib.md5(json.dumps(EN, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:10]


def detect_lang():
    q = request.args.get("lang")
    if q in LANGS:
        return q
    c = request.cookies.get(COOKIE)
    if c in LANGS:
        return c
    for code, _q in request.accept_languages:
        primary = code.split("-")[0].lower()
        if primary == "zh":
            return "zh"
        if primary == "en":
            return "en"
    return "en" if request.accept_languages else "zh"


def current_lang():
    try:
        lang = getattr(g, "lang", None)
        if lang is None:
            lang = g.lang = detect_lang()
        return lang
    except RuntimeError:        # 不在网页请求里（后台线程、命令行）→ 按中文
        return "zh"


def t(text, **params):
    """翻译一句话。中文模式原样返回；英文模式查 EN 字典，查不到就返回中文原文。"""
    if text is None:
        return ""
    is_markup = isinstance(text, Markup)
    out = str(text)
    if current_lang() == "en":
        out = EN.get(out, out)
    if params:
        if any(isinstance(v, Markup) for v in params.values()):
            # 参数里有 HTML（比如 '<b id="x">5</b>'|safe）：句子本身转义，HTML 参数原样保留
            return escape(out).format(**params)
        out = out.format(**params)
    return Markup(out) if is_markup else out


def lang_url(code):
    """当前页面换成另一种语言的链接（保留其它参数）。"""
    args = request.args.to_dict(flat=False)
    args["lang"] = [code]
    return request.path + "?" + urlencode(args, doseq=True)


def init(app):
    @app.before_request
    def _pick_lang():
        g.lang = detect_lang()

    @app.after_request
    def _remember_lang(resp):
        q = request.args.get("lang")
        if q in LANGS and request.cookies.get(COOKIE) != q:
            resp.set_cookie(COOKIE, q, max_age=COOKIE_AGE, samesite="Lax")
        return resp

    @app.context_processor
    def _inject():
        return {"lang": current_lang(), "lang_url": lang_url,
                "i18n_version": _VERSION}

    app.jinja_env.globals["_"] = t

    @app.route("/i18n/<code>.js")
    def i18n_js(code):
        if code not in LANGS:
            code = "zh"
        body = (
            "window.LANG=" + json.dumps(code) + ";"
            "window.I18N=" + json.dumps(_JS_DICT[code], ensure_ascii=False) + ";"
            "window.__=function(s,p){var r=(window.I18N&&Object.prototype.hasOwnProperty.call(I18N,s))?I18N[s]:s;"
            "if(p){for(var k in p){r=r.split('{'+k+'}').join(p[k]);}}return r;};"
        )
        resp = Response(body, mimetype="application/javascript")
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return resp
