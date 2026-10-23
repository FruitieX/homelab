"""Small builder for Grafana dashboard JSON (see generate.py)."""

import json

PROM = {"type": "prometheus", "uid": "prometheus"}
LOKI = {"type": "loki", "uid": "loki"}
INFLUX = {"type": "influxdb", "uid": "influxdb-home"}
PG = {db: {"type": "grafana-postgresql-datasource", "uid": f"pg-{db}"} for db in ("ferrotune", "homectl", "immich")}

# Categorical slots, in fixed order (validated for adjacent-pair CVD
# separation on dark surfaces). Entities keep their slot everywhere.
SLOTS = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"]
GOOD, WARN, BAD, MUTED = "#3aa655", "#e0a100", "#e5484d", "#7d7d7d"

NAV_TAG = "homelab-nav"


# --- targets -----------------------------------------------------------------

def prom(expr, legend="", instant=False, fmt=None, interval=None):
    t = {"datasource": PROM, "expr": expr, "legendFormat": legend, "range": not instant, "instant": instant}
    if fmt:
        t["format"] = fmt
    if interval:
        t["interval"] = interval
    return t


def loki(expr, legend="", instant=False, max_lines=None):
    t = {"datasource": LOKI, "expr": expr, "legendFormat": legend, "queryType": "instant" if instant else "range"}
    if max_lines:
        t["maxLines"] = max_lines
    return t


def flux(query):
    """Flux requires imports before anything else; hoist (and dedupe) them."""
    lines = query.strip().splitlines()
    imports = list(dict.fromkeys(l for l in lines if l.startswith("import ")))
    body = [l for l in lines if not l.startswith("import ")]
    return {"datasource": INFLUX, "query": "\n".join(imports + body).strip()}


def sql(db, query, fmt="table"):
    return {"datasource": PG[db], "rawSql": query.strip(), "format": fmt, "rawQuery": True, "editorMode": "code"}


# --- field config helpers ----------------------------------------------------

def thresholds(*steps):
    """thresholds(GOOD, (80, WARN), (90, BAD)) -> absolute steps."""
    out = [{"color": steps[0], "value": None}]
    out += [{"color": c, "value": v} for v, c in steps[1:]]
    return {"mode": "absolute", "steps": out}


def fixed_colors(mapping):
    """Overrides pinning a series (by display name) to a colour."""
    return [
        {"matcher": {"id": "byName", "options": name},
         "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": color}}]}
        for name, color in mapping.items()
    ]


def by_name(name, **props):
    return {"matcher": {"id": "byName", "options": name},
            "properties": [{"id": k.replace("__", "."), "value": v} for k, v in props.items()]}


def value_map(pairs):
    """pairs: [(value, text, color)] for exact-value mappings."""
    return [{"type": "value", "options": {str(v): {"text": t, "color": c, "index": i}
                                           for i, (v, t, c) in enumerate(pairs)}}]


# --- panels ------------------------------------------------------------------

def _panel(kind, title, targets, desc=None, unit=None, decimals=None, minv=None, maxv=None,
           color=None, thr=None, mappings=None, overrides=None, display_name=None, links=None,
           time_from=None, interval=None, transformations=None, max_dp=None, nulls=None):
    defaults = {"custom": {}}
    if unit:
        defaults["unit"] = unit
    if decimals is not None:
        defaults["decimals"] = decimals
    if minv is not None:
        defaults["min"] = minv
    if maxv is not None:
        defaults["max"] = maxv
    if color:
        defaults["color"] = color
    if thr:
        defaults["thresholds"] = thr
    if mappings:
        defaults["mappings"] = mappings
    if display_name:
        defaults["displayName"] = display_name
    if links:
        defaults["links"] = links
    if nulls:
        defaults["noValue"] = nulls
    p = {"type": kind, "title": title, "targets": [], "fieldConfig": {"defaults": defaults, "overrides": overrides or []},
         "options": {}}
    for i, t in enumerate(targets):
        t = dict(t)
        t.setdefault("refId", chr(ord("A") + i))
        p["targets"].append(t)
    ds = {t["datasource"]["uid"] for t in p["targets"]}
    p["datasource"] = p["targets"][0]["datasource"] if len(ds) == 1 else {"type": "datasource", "uid": "-- Mixed --"}
    if desc:
        p["description"] = desc
    if time_from:
        p["timeFrom"] = time_from
    if interval:
        p["interval"] = interval
    if transformations:
        p["transformations"] = transformations
    if max_dp:
        p["maxDataPoints"] = max_dp
    return p


def timeseries(title, targets, *, stack=False, fill=12, bars=False, legend="bottom", legend_calcs=None,
               line=2, points="never", thresholds_style=None, gradient="opacity", spans=True, **kw):
    p = _panel("timeseries", title, targets, **kw)
    p["fieldConfig"]["defaults"].setdefault("color", {"mode": "palette-classic"})
    p["fieldConfig"]["defaults"]["custom"] = {
        "drawStyle": "bars" if bars else "line",
        "lineWidth": 1 if bars else line,
        "fillOpacity": 80 if bars else fill,
        "gradientMode": "none" if bars else gradient,
        "lineInterpolation": "smooth" if not bars else "linear",
        "showPoints": points,
        "spanNulls": spans if not bars else False,
        "stacking": {"mode": "normal" if stack else "none", "group": "A"},
        "axisBorderShow": False,
        "barAlignment": -1,
        "thresholdsStyle": {"mode": thresholds_style or "off"},
        "insertNulls": False if bars else 3600000,
    }
    p["options"] = {
        "legend": {"displayMode": "list" if not legend_calcs else "table", "placement": legend,
                   "showLegend": legend != "hidden", "calcs": legend_calcs or []},
        "tooltip": {"mode": "multi", "sort": "desc"},
    }
    return p


def stat(title, targets, *, graph=True, color_mode="value", text_mode="value", reduce="lastNotNull",
         orientation="auto", justify="auto", **kw):
    p = _panel("stat", title, targets, **kw)
    p["fieldConfig"]["defaults"].setdefault("color", {"mode": "thresholds"})
    p["fieldConfig"]["defaults"].setdefault("thresholds", thresholds(SLOTS[0]))
    p["options"] = {
        "reduceOptions": {"calcs": [reduce], "fields": "", "values": False},
        "graphMode": "area" if graph else "none",
        "colorMode": color_mode,
        "textMode": text_mode,
        "justifyMode": justify,
        "orientation": orientation,
        "wideLayout": True,
        "showPercentChange": False,
    }
    return p


def bargauge(title, targets, *, mode="basic", orientation="horizontal", reduce="lastNotNull",
             show_unfilled=True, values=False, min_size=None, **kw):
    p = _panel("bargauge", title, targets, **kw)
    p["fieldConfig"]["defaults"].setdefault("color", {"mode": "thresholds"})
    p["fieldConfig"]["defaults"].setdefault("thresholds", thresholds(SLOTS[0]))
    p["options"] = {
        "reduceOptions": {"calcs": [reduce], "fields": "", "values": values},
        "displayMode": mode, "orientation": orientation, "showUnfilled": show_unfilled,
        "valueMode": "color", "namePlacement": "left", "sizing": "auto",
        "minVizHeight": 12, "minVizWidth": 8, "maxVizHeight": 28,
    }
    if min_size:
        p["options"]["sizing"] = "manual"
        p["options"]["minVizHeight"] = min_size
    return p


def table(title, targets, *, sort=None, footer=None, cell_height="sm", **kw):
    p = _panel("table", title, targets, **kw)
    p["fieldConfig"]["defaults"]["custom"] = {"align": "auto", "cellOptions": {"type": "auto"}, "inspect": False}
    p["options"] = {"showHeader": True, "cellHeight": cell_height,
                    "footer": {"show": bool(footer), "reducer": footer or ["sum"], "countRows": False, "fields": ""}}
    if sort:
        p["options"]["sortBy"] = [{"displayName": sort[0], "desc": sort[1]}]
    return p


def status_history(title, targets, *, show_values="never", row_height=0.9, legend=False, **kw):
    p = _panel("status-history", title, targets, **kw)
    p["fieldConfig"]["defaults"]["custom"] = {"lineWidth": 0, "fillOpacity": 85}
    p["options"] = {"showValue": show_values, "rowHeight": row_height, "colWidth": 0.95,
                    "legend": {"showLegend": legend, "displayMode": "list", "placement": "bottom"},
                    "tooltip": {"mode": "single", "sort": "none"}}
    return p


def state_timeline(title, targets, *, show_values="never", row_height=0.85, merge=True, **kw):
    p = _panel("state-timeline", title, targets, **kw)
    p["fieldConfig"]["defaults"]["custom"] = {"lineWidth": 0, "fillOpacity": 80}
    p["options"] = {"showValue": show_values, "rowHeight": row_height, "mergeValues": merge,
                    "alignValue": "left", "legend": {"showLegend": False},
                    "tooltip": {"mode": "single", "sort": "none"}}
    return p


def barchart(title, targets, *, x_field=None, stack=False, legend="hidden", label_rotation=0,
             label_spacing=0, show_values="never", group_width=0.82, bar_width=0.9, **kw):
    p = _panel("barchart", title, targets, **kw)
    p["fieldConfig"]["defaults"]["custom"] = {"fillOpacity": 85, "lineWidth": 0, "gradientMode": "none",
                                              "axisBorderShow": False}
    p["options"] = {"orientation": "vertical", "showValue": show_values, "stacking": "normal" if stack else "none",
                    "groupWidth": group_width, "barWidth": bar_width, "barRadius": 0.1,
                    "xTickLabelRotation": label_rotation, "xTickLabelSpacing": label_spacing,
                    "legend": {"showLegend": legend != "hidden", "displayMode": "list", "placement": "bottom"},
                    "tooltip": {"mode": "single", "sort": "none"}}
    if x_field:
        p["options"]["xField"] = x_field
    return p


def logs(title, targets, **kw):
    p = _panel("logs", title, targets, **kw)
    p["options"] = {"showTime": True, "wrapLogMessage": True, "sortOrder": "Descending",
                    "enableLogDetails": True, "dedupStrategy": "none", "prettifyLogMessage": False}
    return p


def text(title, content, mode="markdown"):
    return {"type": "text", "title": title, "options": {"mode": mode, "content": content.strip()},
            "transparent": True}


# --- transformations ---------------------------------------------------------

def merge():
    return {"id": "merge", "options": {}}


def organize(rename=None, exclude=(), order=None):
    opts = {"renameByName": rename or {}, "excludeByName": {k: True for k in exclude}}
    if order:
        opts["indexByName"] = {k: i for i, k in enumerate(order)}
    return {"id": "organize", "options": opts}


def labels_to_fields(value_label=None):
    o = {"mode": "columns"}
    if value_label:
        o["valueLabel"] = value_label
    return {"id": "labelsToFields", "options": o}


def reduce_rows(calcs):
    return {"id": "reduce", "options": {"reducers": calcs, "mode": "seriesToRows", "includeTimeField": False}}


def sort_by(field, desc=False):
    return {"id": "sortBy", "options": {"sort": [{"field": field, "desc": desc}]}}


def filter_values(field, op, value):
    return {"id": "filterByValue", "options": {"type": "include", "match": "all", "filters": [
        {"fieldName": field, "config": {"id": op, "options": {"value": value}}}]}}


# --- dashboard ---------------------------------------------------------------

class Dashboard:
    def __init__(self, uid, title, *, folder, tags=(), time="now-24h", refresh="1m", description="",
                 annotations=None, variables=None):
        self.uid, self.title, self.folder = uid, title, folder
        self.tags = [NAV_TAG, *tags]
        self.time, self.refresh, self.description = time, refresh, description
        self.annotations = annotations or []
        self.variables = variables or []
        self.panels, self._x, self._y, self._row_h, self._id = [], 0, 0, 0, 1

    def row(self, title, collapsed=False):
        self.newline()
        self.panels.append({"type": "row", "title": title, "collapsed": collapsed, "id": self._id,
                            "gridPos": {"h": 1, "w": 24, "x": 0, "y": self._y}, "panels": []})
        self._id += 1
        self._y += 1
        return self

    def newline(self):
        if self._x:
            self._y += self._row_h
            self._x, self._row_h = 0, 0

    def add(self, panel, w, h):
        if self._x + w > 24:
            self.newline()
        panel = dict(panel)
        panel["id"] = self._id
        self._id += 1
        panel["gridPos"] = {"h": h, "w": w, "x": self._x, "y": self._y}
        self.panels.append(panel)
        self._x += w
        self._row_h = max(self._row_h, h)
        if self._x >= 24:
            self.newline()
        return self

    def to_json(self):
        anns = [{"builtIn": 1, "datasource": {"type": "grafana", "uid": "-- Grafana --"}, "enable": True,
                 "hide": True, "iconColor": "rgba(0, 211, 255, 1)", "name": "Annotations & Alerts",
                 "type": "dashboard"}] + self.annotations
        return {
            "uid": self.uid, "title": self.title, "description": self.description, "tags": self.tags,
            "timezone": "browser", "editable": True, "graphTooltip": 1, "schemaVersion": 39,
            "time": {"from": self.time, "to": "now"}, "refresh": self.refresh, "fiscalYearStartMonth": 0,
            "liveNow": False, "weekStart": "monday",
            "links": [{"type": "dashboards", "tags": [NAV_TAG], "asDropdown": False, "includeVars": False,
                       "keepTime": True, "title": "Dashboards", "icon": "external link", "targetBlank": False}],
            "annotations": {"list": anns},
            "templating": {"list": self.variables},
            "panels": self.panels,
        }

    def dumps(self):
        return json.dumps(self.to_json(), indent=1, ensure_ascii=False) + "\n"


def deploy_annotation(namespace="default"):
    return {
        "datasource": PROM, "enable": True, "hide": False, "iconColor": SLOTS[6], "name": "Deploys",
        "expr": (f'max by (namespace, owner_name) ((time() - kube_replicaset_created{{namespace=~"{namespace}"}} < 90) '
                 '* on (namespace, replicaset) group_left (owner_name) kube_replicaset_owner{owner_kind="Deployment"})'),
        "step": "60s", "titleFormat": "Rollout", "textFormat": "{{owner_name}} rolled out",
        "tagKeys": "namespace",
    }
