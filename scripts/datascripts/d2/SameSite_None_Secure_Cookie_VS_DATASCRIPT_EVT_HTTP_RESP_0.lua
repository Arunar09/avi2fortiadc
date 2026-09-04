-- =============================================
-- Avi DataScript -> FortiADC Lua Migration
-- Original Name : SameSite_None_Secure_Cookie
-- Avi Event     : VS_DATASCRIPT_EVT_HTTP_RESP
-- Reference     : https://docs.fortinet.com/document/fortiadc/8.0.0/script-reference-guide/144544/overview
-- =============================================

-- HTTP_RESPONSE
headers = avi.http.get_header()
avi.http.remove_header("Set-Cookie")
for k, v in pairs(headers) do
    if (string.lower(k) == "set-cookie") then
        if (type(v) == "string") then
            -- If there's only one SameSite cookie header then v is a string
            -- v needs to be converted to a table
            v = {v}
        end

        for key, val in pairs(v) do
            -- only modify if Set-Cookie header does not have SameSite attribute
            new_val = val
            if not string.find(string.lower(val), "samesite") then
                new_val = val .."; samesite=None"
            end
            if not string.find(string.lower(new_val), "secure") then
                new_val = new_val .."; secure"
            end
            avi.http.add_header("Set-Cookie", new_val)
        end
    end
end