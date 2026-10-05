<?php

namespace Grocy\Middleware\Auth;

use Psr\Http\Message\ServerRequestInterface as Request;

// Browser requests keep Grocy's trusted-header provisioning. API-key requests
// must never fall back to an identity header when the key is rejected.
class Mu3labReverseProxyAuthMiddleware extends ReverseProxyAuthMiddleware
{
    public function AuthenticateRequest(Request $request)
    {
        $identity = $request->getHeader(GROCY_REVERSE_PROXY_AUTH_HEADER);
        $hasIdentity = count($identity) === 1 && strlen($identity[0]) > 0;
        if ($this->IsApiRoute($request) && ($request->hasHeader('GROCY-API-KEY') || !$hasIdentity)) {
            define('GROCY_EXTERNALLY_MANAGED_AUTHENTICATION', true);
            $auth = new ApiKeyAuthMiddleware($this->AppContainer, $this->ResponseFactory);
            // BaseAuthMiddleware turns null into HTTP 401. Do not catch database
            // or application errors: those must still surface as server errors.
            return $auth->AuthenticateRequest($request);
        }

        return parent::AuthenticateRequest($request);
    }
}
