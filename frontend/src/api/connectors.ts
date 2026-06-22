import { api } from "./client";

// /api/connectors (in main.py) → which optional connectors are enabled on this server.
export const getConnectors = () => api<{ enabled: { name: string }[] }>("/connectors");
