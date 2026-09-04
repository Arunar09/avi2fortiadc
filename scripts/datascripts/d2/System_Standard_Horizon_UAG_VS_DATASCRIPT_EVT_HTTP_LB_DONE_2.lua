-- =============================================
-- Avi DataScript -> FortiADC Lua Migration
-- Original Name : System-Standard-Horizon-UAG
-- Avi Event     : VS_DATASCRIPT_EVT_HTTP_LB_DONE
-- Reference     : https://docs.fortinet.com/document/fortiadc/8.0.0/script-reference-guide/144544/overview
-- =============================================

uag_fqdn = '' -- replace this variable with the site-specific UAG VS VIP FQDN for GSLB usecase
if uag_fqdn == '' then
	uag_fqdn = avi.http.hostname()
end
avi.http.set_reqvar('uag_fqdn', uag_fqdn)
vs_port = avi.vs.port()
if vs_port == '443' then
	primary_port = avi.horizon.get_server_ports(avi.horizon.PRIMARY_PORT)
	uri = avi.http.get_uri()
	redirect_host = 'https://'..uag_fqdn..':'..primary_port
	avi.http.response(307, {location=redirect_host..uri})
end