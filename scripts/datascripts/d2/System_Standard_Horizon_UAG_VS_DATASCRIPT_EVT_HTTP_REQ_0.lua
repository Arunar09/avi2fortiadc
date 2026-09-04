-- =============================================
-- Avi DataScript -> FortiADC Lua Migration
-- Original Name : System-Standard-Horizon-UAG
-- Avi Event     : VS_DATASCRIPT_EVT_HTTP_REQ
-- Reference     : https://docs.fortinet.com/document/fortiadc/8.0.0/script-reference-guide/144544/overview
-- =============================================

local function starts_with(str, start)
	return str:sub(1, #start) == start
end

if starts_with(avi.http.get_uri(), "/ice/tunnel") then
	avi.http.set_request_body_buffer_size(0)
end