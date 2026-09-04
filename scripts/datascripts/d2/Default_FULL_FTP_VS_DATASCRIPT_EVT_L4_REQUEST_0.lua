-- =============================================
-- Avi DataScript -> FortiADC Lua Migration
-- Original Name : Default-FULL-FTP
-- Avi Event     : VS_DATASCRIPT_EVT_L4_REQUEST
-- Reference     : https://docs.fortinet.com/document/fortiadc/8.0.0/script-reference-guide/144544/overview
-- =============================================

-- Hanlde PORT client_ip, port (rewrite client ip -> vip, port - > ephemeral port)
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
local p1, p2, tail = string.match(payload, 'PORT %d+,%d+,%d+,%d+,(%d+),(%d+)(\r\n)')
local stringOne = 20
local stringTwo = 20
if p1 ~= nil then
   if avi.l4.get_active_ftp_port() ~= 0 then
         stringOne= math.floor(avi.l4.get_active_ftp_port()/256)
         stringTwo = avi.l4.get_active_ftp_port() % 256
    end

   avi.l4.set_active_ftp_port(p1*256 + p2)
   local vip_ip = string.gsub(avi.vs.se_intf_ip(), '%.', ',')
   local rewrite = 'PORT ' .. vip_ip .. ',' .. stringOne .. ',' .. stringTwo .. tail
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
