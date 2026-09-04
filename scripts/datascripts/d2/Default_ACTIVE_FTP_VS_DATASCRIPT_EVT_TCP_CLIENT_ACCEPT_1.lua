-- =============================================
-- Avi DataScript -> FortiADC Lua Migration
-- Original Name : Default-ACTIVE-FTP
-- Avi Event     : VS_DATASCRIPT_EVT_TCP_CLIENT_ACCEPT
-- Reference     : https://docs.fortinet.com/document/fortiadc/8.0.0/script-reference-guide/144544/overview
-- =============================================

avi.l4.do_lb(true)
