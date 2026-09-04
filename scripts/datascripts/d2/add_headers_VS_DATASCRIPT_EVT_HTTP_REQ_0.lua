-- =============================================
-- Avi DataScript -> FortiADC Lua Migration
-- Original Name : add-headers
-- Avi Event     : VS_DATASCRIPT_EVT_HTTP_REQ
-- Reference     : https://docs.fortinet.com/document/fortiadc/8.0.0/script-reference-guide/144544/overview
-- =============================================

host = avi.http.get_header("Host")
port = avi.vs.port()
protocol = avi.http.protocol()
if protocol and port then
    avi.http.add_header( 'Forwarded', host..':'..port )
    avi.http.add_header( 'proto', protocol )
    avi.http.add_header( 'WL-Proxy-SSL', 'true' )
end