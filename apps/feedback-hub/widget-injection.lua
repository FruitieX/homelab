-- Injects the feedback-hub widget <script> into HTML pages of opted-in private
-- apps. Parameterized per route through filterContext: { app = "...", hub = "https://..." }.
--
-- Only GET page loads from private (LAN/VPN) clients are touched. For those the
-- request's Accept-Encoding and conditional headers are dropped so the HTML
-- arrives whole and uncompressed and can be rewritten; assets and API calls are left alone and never buffered.

local function is_private(address)
  if not address then return false end
  local ip = address:match("^%[(.-)%]") or address:match("^([^:]+):%d+$") or address
  local a, b = ip:match("^(%d+)%.(%d+)%.%d+%.%d+$")
  if a then
    a, b = tonumber(a), tonumber(b)
    return a == 10 or a == 127 or (a == 192 and b == 168) or (a == 172 and b >= 16 and b <= 31)
      or (a == 100 and b >= 64 and b <= 127) -- CGNAT range used by Tailscale and similar VPNs
  end
  ip = ip:lower()
  return ip == "::1" or ip:match("^f[cd]") ~= nil or ip:match("^::ffff:10%.") ~= nil
    or ip:match("^::ffff:192%.168%.") ~= nil
end

-- Precache/service-worker requests for index.html don't send Accept: text/html,
-- so extensionless paths are treated as pages too.
local function looks_like_page(path)
  path = path:gsub("%?.*$", "")
  if path:match("^/api/") or path:match("^/rest/") or path:match("^/ws") then return false end
  local last = path:match("([^/]*)$") or ""
  return last == "" or last:match("%.html?$") ~= nil or not last:find(".", 1, true)
end

-- Runs before the app's own scripts: WebGL canvases (maps, Pixi floorplans) keep
-- their pixels so the widget's page snapshot can read them. Keep the `__fhGl`
-- flag in sync with preserveWebGlBuffers() in feedback-hub's widget.ts.
local EARLY_PATCH = [==[<script>(function(){var w=window,p=HTMLCanvasElement.prototype,g=p.getContext;if(w.__fhGl)return;w.__fhGl=1;p.getContext=function(t,a){if(/webgl/.test(t))a=Object.assign({},a,{preserveDrawingBuffer:true});return g.call(this,t,a)}})();</script>]==]

local function after_head_open(html)
  local _, e = html:find("<head>", 1, true)
  if not e then _, e = html:find("<[Hh][Ee][Aa][Dd]%s[^>]*>") end
  if not e then _, e = html:find("<HEAD>", 1, true) end
  return e
end

function envoy_on_request(handle)
  local headers = handle:headers()
  if headers:get(":method") ~= "GET" or headers:get("upgrade") then return end
  local ok, app, hub = pcall(function()
    local context = handle:filterContext()
    return context:get("app"), context:get("hub")
  end)
  if not ok or not app or not hub then return end
  local accept = headers:get("accept") or ""
  if not (accept:find("text/html", 1, true) or looks_like_page(headers:get(":path") or "/")) then
    return
  end
  if not is_private(handle:streamInfo():downstreamDirectRemoteAddress()) then return end
  headers:remove("accept-encoding")
  -- Always fetch the full page: a 304 can't be rewritten, so a browser holding a
  -- copy from before injection (or from another network) would keep it forever.
  headers:remove("if-none-match")
  headers:remove("if-modified-since")
  handle:streamInfo():dynamicMetadata():set("feedback.widget", "app", app)
  handle:streamInfo():dynamicMetadata():set("feedback.widget", "hub", hub)
end

function envoy_on_response(handle)
  local meta = handle:streamInfo():dynamicMetadata():get("feedback.widget")
  if not meta or not meta.app then return end
  local headers = handle:headers()
  local content_type = headers:get("content-type") or ""
  if not content_type:find("text/html", 1, true) or headers:get("content-encoding") then return end
  local length = tonumber(headers:get("content-length") or "")
  if length and length > 2000000 then return end

  local body = handle:body()
  if not body then return end
  local html = body:getBytes(0, body:length())
  if html:find("feedback-hub-widget", 1, true) then return end
  local tag = string.format('<script async src="%s/widget.js" data-app="%s" id="feedback-hub-widget"></script>',
    meta.hub, meta.app)
  local at = html:find("</head>", 1, true) or html:find("</HEAD>", 1, true)
    or html:find("</body>", 1, true) or html:find("</BODY>", 1, true)
  local out = at and (html:sub(1, at - 1) .. tag .. html:sub(at)) or (html .. tag)
  local head = after_head_open(out)
  out = head and (out:sub(1, head) .. EARLY_PATCH .. out:sub(head + 1)) or (EARLY_PATCH .. out)
  body:setBytes(out)
  -- body() yields, which invalidates the earlier headers handle.
  headers = handle:headers()
  headers:replace("content-length", tostring(#out))
  -- The rewritten page differs per client network; keep caches from mixing them.
  headers:remove("etag")
  headers:replace("cache-control", "no-cache")
end
