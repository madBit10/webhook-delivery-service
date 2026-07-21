export const API_BASE = "http://localhost:8000";

//shard shape of an event coming back from the API

export type WebhookEvent = {
    id: number;
    endpoint_id: number;
    event_type: string;
    payload: Record<string, unknown>;
    status: "pending" | "delivered" | "dead";
    created_at: string;
}