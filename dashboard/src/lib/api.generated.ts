export interface paths {
    "/api/overview": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: {
            parameters: {
                query?: never;
                header?: never;
                path?: never;
                cookie?: never;
            };
            requestBody?: never;
            responses: {
                /** @description Success */
                200: {
                    headers: {
                        [name: string]: unknown;
                    };
                    content: {
                        "application/json": {
                            /** Format: date-time */
                            as_of: string;
                            session_state: string;
                            /** @enum {string} */
                            trading_mode: "paper" | "live";
                            broker_healthy: boolean;
                            feed_healthy: boolean;
                            worker_healthy: boolean;
                            stale_after_seconds: number;
                            kill_switch: boolean;
                            day_pnl: string | null;
                            realised_pnl: string | null;
                            unrealised_pnl: string | null;
                            open_positions: number;
                            open_orders: number;
                            equity_curve: {
                                /** Format: date-time */
                                at: string;
                                value: number;
                            }[];
                            events: {
                                id: string;
                                /** Format: date-time */
                                at: string;
                                /** @enum {string} */
                                severity: "info" | "warning" | "error";
                                message: string;
                            }[];
                        };
                    };
                };
            };
        };
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/positions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: {
            parameters: {
                query?: never;
                header?: never;
                path?: never;
                cookie?: never;
            };
            requestBody?: never;
            responses: {
                /** @description Success */
                200: {
                    headers: {
                        [name: string]: unknown;
                    };
                    content: {
                        "application/json": {
                            /** Format: date-time */
                            as_of: string;
                            items: {
                                id: string;
                                instrument_id: string;
                                symbol: string;
                                /** @enum {string} */
                                exchange: "NSE" | "BSE";
                                strategy_id: string;
                                quantity: number;
                                average_price: string;
                                last_price: string | null;
                                unrealised_pnl: string | null;
                                realised_pnl: string;
                                frozen: boolean;
                            }[];
                        };
                    };
                };
            };
        };
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/orders": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: {
            parameters: {
                query?: never;
                header?: never;
                path?: never;
                cookie?: never;
            };
            requestBody?: never;
            responses: {
                /** @description Success */
                200: {
                    headers: {
                        [name: string]: unknown;
                    };
                    content: {
                        "application/json": {
                            /** Format: date-time */
                            as_of: string;
                            items: {
                                id: string;
                                symbol: string;
                                /** @enum {string} */
                                exchange: "NSE" | "BSE";
                                /** @enum {string} */
                                side: "BUY" | "SELL";
                                /** @enum {string} */
                                order_type: "LIMIT" | "STOPLOSS_LIMIT";
                                quantity: number;
                                filled_quantity: number;
                                limit_price: string;
                                state: string;
                                /** Format: date-time */
                                created_at: string;
                                events: {
                                    sequence: number;
                                    state: string;
                                    /** Format: date-time */
                                    at: string;
                                    message: string | null;
                                }[];
                            }[];
                        };
                    };
                };
            };
        };
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/strategies": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: {
            parameters: {
                query?: never;
                header?: never;
                path?: never;
                cookie?: never;
            };
            requestBody?: never;
            responses: {
                /** @description Success */
                200: {
                    headers: {
                        [name: string]: unknown;
                    };
                    content: {
                        "application/json": {
                            /** Format: date-time */
                            as_of: string;
                            items: {
                                id: string;
                                name: string;
                                /** @enum {string} */
                                status: "running" | "stopped" | "halted" | "starting" | "stopping";
                                pnl: string | null;
                                signals: number;
                                description: string;
                                config: {
                                    [key: string]: unknown;
                                };
                            }[];
                        };
                    };
                };
            };
        };
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/risk": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: {
            parameters: {
                query?: never;
                header?: never;
                path?: never;
                cookie?: never;
            };
            requestBody?: never;
            responses: {
                /** @description Success */
                200: {
                    headers: {
                        [name: string]: unknown;
                    };
                    content: {
                        "application/json": {
                            /** Format: date-time */
                            as_of: string;
                            limits: {
                                name: string;
                                used: number;
                                limit: number;
                                unit: string;
                            }[];
                            events: {
                                id: string;
                                /** Format: date-time */
                                at: string;
                                /** @enum {string} */
                                severity: "info" | "warning" | "error";
                                message: string;
                            }[];
                        };
                    };
                };
            };
        };
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/market": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: {
            parameters: {
                query?: never;
                header?: never;
                path?: never;
                cookie?: never;
            };
            requestBody?: never;
            responses: {
                /** @description Success */
                200: {
                    headers: {
                        [name: string]: unknown;
                    };
                    content: {
                        "application/json": {
                            /** Format: date-time */
                            as_of: string;
                            items: {
                                instrument_id: string;
                                symbol: string;
                                /** @enum {string} */
                                exchange: "NSE" | "BSE";
                                last_price: string | null;
                                change_percent: number | null;
                                /** Format: date-time */
                                as_of: string;
                            }[];
                        };
                    };
                };
            };
        };
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/system": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: {
            parameters: {
                query?: never;
                header?: never;
                path?: never;
                cookie?: never;
            };
            requestBody?: never;
            responses: {
                /** @description Success */
                200: {
                    headers: {
                        [name: string]: unknown;
                    };
                    content: {
                        "application/json": {
                            /** Format: date-time */
                            as_of: string;
                            services: {
                                name: string;
                                healthy: boolean;
                                detail: string;
                            }[];
                            reconciliations: {
                                id: string;
                                /** Format: date-time */
                                at: string;
                                /** @enum {string} */
                                severity: "info" | "warning" | "error";
                                message: string;
                            }[];
                            events: {
                                id: string;
                                /** Format: date-time */
                                at: string;
                                /** @enum {string} */
                                severity: "info" | "warning" | "error";
                                message: string;
                            }[];
                            commands: {
                                id: string;
                                idempotency_key: string;
                                type: string;
                                /** @enum {string} */
                                status: "PENDING" | "ACCEPTED" | "EXECUTING" | "DONE" | "FAILED" | "REJECTED" | "EXPIRED";
                                /** Format: date-time */
                                created_at: string;
                                message: string | null;
                            }[];
                        };
                    };
                };
            };
        };
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/auth/login": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post: {
            parameters: {
                query?: never;
                header?: never;
                path?: never;
                cookie?: never;
            };
            requestBody: {
                content: {
                    "application/json": {
                        passcode: string;
                    };
                };
            };
            responses: {
                /** @description Success */
                200: {
                    headers: {
                        [name: string]: unknown;
                    };
                    content: {
                        "application/json": {
                            access_token: string;
                            /** @constant */
                            token_type: "bearer";
                            expires_in: number;
                        };
                    };
                };
            };
        };
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/commands": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post: {
            parameters: {
                query?: never;
                header?: never;
                path?: never;
                cookie?: never;
            };
            requestBody: {
                content: {
                    "application/json": {
                        /** @constant */
                        type: "SET_KILL_SWITCH";
                        params: {
                            /** @constant */
                            halted: true;
                            reason: string;
                        };
                        /** Format: uuid */
                        idempotency_key: string;
                    } | {
                        /** @constant */
                        type: "SQUARE_OFF_ALL";
                        params: Record<string, never>;
                        /** Format: uuid */
                        idempotency_key: string;
                    } | {
                        /** @constant */
                        type: "CLOSE_POSITION";
                        params: {
                            instrument_id: string;
                        };
                        /** Format: uuid */
                        idempotency_key: string;
                    } | {
                        /** @constant */
                        type: "CANCEL_ORDER";
                        params: {
                            order_id: string;
                        };
                        /** Format: uuid */
                        idempotency_key: string;
                    } | {
                        /** @constant */
                        type: "START_STRATEGY";
                        params: {
                            name: string;
                        };
                        /** Format: uuid */
                        idempotency_key: string;
                    } | {
                        /** @constant */
                        type: "STOP_STRATEGY";
                        params: {
                            name: string;
                        };
                        /** Format: uuid */
                        idempotency_key: string;
                    } | {
                        /** @constant */
                        type: "UPDATE_STRATEGY_CONFIG";
                        params: {
                            name: string;
                            config: {
                                [key: string]: unknown;
                            };
                            /** @constant */
                            force: false;
                        };
                        /** Format: uuid */
                        idempotency_key: string;
                    } | {
                        /** @constant */
                        type: "PLACE_MANUAL_ORDER";
                        params: {
                            instrument_id: string;
                            /** @enum {string} */
                            side: "BUY" | "SELL";
                            quantity: number;
                            limit_price: string;
                            reason: string;
                        };
                        /** Format: uuid */
                        idempotency_key: string;
                    } | {
                        /** @constant */
                        type: "RECONCILE_NOW";
                        params: Record<string, never>;
                        /** Format: uuid */
                        idempotency_key: string;
                    };
                };
            };
            responses: {
                /** @description Success */
                202: {
                    headers: {
                        [name: string]: unknown;
                    };
                    content: {
                        "application/json": {
                            id: string;
                            idempotency_key: string;
                            type: string;
                            /** @enum {string} */
                            status: "PENDING" | "ACCEPTED" | "EXECUTING" | "DONE" | "FAILED" | "REJECTED" | "EXPIRED";
                            /** Format: date-time */
                            created_at: string;
                            message: string | null;
                        };
                    };
                };
            };
        };
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/commands/{idempotency_key}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: {
            parameters: {
                query?: never;
                header?: never;
                path: {
                    idempotency_key: string;
                };
                cookie?: never;
            };
            requestBody?: never;
            responses: {
                /** @description Success */
                200: {
                    headers: {
                        [name: string]: unknown;
                    };
                    content: {
                        "application/json": {
                            id: string;
                            idempotency_key: string;
                            type: string;
                            /** @enum {string} */
                            status: "PENDING" | "ACCEPTED" | "EXECUTING" | "DONE" | "FAILED" | "REJECTED" | "EXPIRED";
                            /** Format: date-time */
                            created_at: string;
                            message: string | null;
                        };
                    };
                };
            };
        };
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/candles": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: {
            parameters: {
                query: {
                    instrument_id: string;
                    interval: "1m" | "5m" | "15m";
                };
                header?: never;
                path?: never;
                cookie?: never;
            };
            requestBody?: never;
            responses: {
                /** @description Success */
                200: {
                    headers: {
                        [name: string]: unknown;
                    };
                    content: {
                        "application/json": {
                            items: {
                                time: number;
                                open: number;
                                high: number;
                                low: number;
                                close: number;
                            }[];
                        };
                    };
                };
            };
        };
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/events": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get: {
            parameters: {
                query?: never;
                header?: never;
                path?: never;
                cookie?: never;
            };
            requestBody?: never;
            responses: {
                /** @description SSE invalidations; data: {"resource":"orders"}. Heartbeat every <=15 seconds. */
                200: {
                    headers: {
                        [name: string]: unknown;
                    };
                    content: {
                        "text/event-stream": string;
                    };
                };
            };
        };
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: never;
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export type operations = Record<string, never>;
