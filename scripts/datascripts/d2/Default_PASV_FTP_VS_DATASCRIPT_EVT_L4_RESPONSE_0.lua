-- =============================================
-- Avi DataScript -> FortiADC Lua Migration
-- Original Name : Default-PASV-FTP
-- Avi Event     : VS_DATASCRIPT_EVT_L4_RESPONSE
-- Reference     : https://docs.fortinet.com/document/fortiadc/8.0.0/script-reference-guide/144544/overview
-- =============================================

-- Handle passive FTP 227 response rewrite (server IP to VIP)
function string.tohex(str)
   return (str:gsub('.', function (c)
       return string.format('%02X', string.byte(c))
   end))
end
-- Do not run DS for data ports
if avi.vs.port() ~= '21' then
   avi.l4.ds_done()
end
-- Read entire payload (assumption that entire response we are looking for is a single packet)
local payload = avi.l4.read()
local p1, p2, tail = string.match(payload, '227 Entering Passive Mode %(%d+,%d+,%d+,%d+,(%d+),(%d+)%)(.?\r\n)')
if p1 ~= nil then
   avi.l4.open_passive_ftp_port(p1*256+p2)   local vip_ip = string.gsub(avi.vs.ip(), '%.', ',')
   local client_port = tonumber(avi.vs.client_port())   p1 = (client_port) / 256   p1 = math.floor(p1)   p2 = client_port - (p1*256)   local rewrite = '227 Entering Passive Mode (' .. vip_ip .. ',' .. p1 .. ',' .. p2 .. ')' .. tail
   avi.l4.modify(string.tohex(rewrite))
   rewrite_len = rewrite:len()
   payload_len = payload:len()
   if rewrite_len < payload_len then
       avi.l4.discard(payload_len-rewrite_len, rewrite_len)
   end
else
   local usr = string.match(payload, 'USER')
   if usr ~= nil then 
       avi.vs.log(payload)
   end
end
