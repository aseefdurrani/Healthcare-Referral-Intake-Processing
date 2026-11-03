import type {
  ProblemBody,
  ReferralDetail,
  ReferralPage,
  ReferralStatusBody,
  ReferralSummary,
} from "./types";

export interface paths {
  "/me": {
    get: {
      responses: {
        200: { content: { "application/json": { user_id: string; username: "staff" } } };
        401: { content: { "application/problem+json": ProblemBody } };
        403: { content: { "application/problem+json": ProblemBody } };
      };
    };
  };
  "/referrals": {
    get: {
      parameters: { query?: { limit?: number; offset?: number; status?: string } };
      responses: {
        200: { content: { "application/json": ReferralPage } };
      };
    };
    post: {
      responses: {
        202: { content: { "application/json": ReferralSummary } };
      };
    };
  };
  "/referrals/{id}": {
    get: {
      parameters: { path: { id: string } };
      responses: { 200: { content: { "application/json": ReferralDetail } } };
    };
    patch: {
      parameters: { path: { id: string } };
      responses: { 200: { content: { "application/json": ReferralDetail } } };
    };
  };
  "/referrals/{id}/status": {
    get: {
      parameters: { path: { id: string } };
      responses: { 200: { content: { "application/json": ReferralStatusBody } } };
    };
  };
  "/referrals/{id}/approve": {
    post: {
      parameters: { path: { id: string } };
      responses: { 200: { content: { "application/json": ReferralDetail } } };
    };
  };
  "/referrals/{id}/reject": {
    post: {
      parameters: { path: { id: string } };
      responses: { 200: { content: { "application/json": ReferralDetail } } };
    };
  };
  "/referrals/{id}/retry": {
    post: {
      parameters: { path: { id: string } };
      responses: { 202: { content: { "application/json": ReferralSummary } } };
    };
  };
}
