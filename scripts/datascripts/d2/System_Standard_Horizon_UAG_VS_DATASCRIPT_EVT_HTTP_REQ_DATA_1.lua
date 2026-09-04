-- =============================================
-- Avi DataScript -> FortiADC Lua Migration
-- Original Name : System-Standard-Horizon-UAG
-- Avi Event     : VS_DATASCRIPT_EVT_HTTP_REQ_DATA
-- Reference     : https://docs.fortinet.com/document/fortiadc/8.0.0/script-reference-guide/144544/overview
-- =============================================

local avixmlparser = require("avixmlparser")
local body = avi.http.get_req_body(2048)
local xpath = "/broker/do-submit-authentication/screen[name='windows-password']/params/param[name='username']/values/value"
local succ_parse, document = avixmlparser.parse(body)
if succ_parse then
	local succ_search, contents = avixmlparser.search(document, xpath)
	if succ_search then
		for i, v in ipairs(contents) do
			if v ~= nil then
			  avi.http.set_userid(v)
			  break
			end
		end
	end
end