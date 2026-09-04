-- =============================================
-- Avi DataScript -> FortiADC Lua Migration
-- Original Name : clientip.ds
-- Avi Event     : VS_DATASCRIPT_EVT_HTTP_REQ
-- Reference     : https://docs.fortinet.com/document/fortiadc/8.0.0/script-reference-guide/144544/overview
-- =============================================

avi.http.add_header( 'X-Forwarded-For', avi.vs.client_ip())