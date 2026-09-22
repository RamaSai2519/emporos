export interface paths {
    "/health": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Health */
        get: operations["health_health_get"];
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
        /** Login */
        post: operations["login_auth_login_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/overview": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Overview */
        get: operations["overview_overview_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/positions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Positions */
        get: operations["positions_positions_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/orders": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Orders */
        get: operations["orders_orders_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/orders/{order_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Order */
        get: operations["order_orders__order_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/executions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Executions */
        get: operations["executions_executions_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/strategies": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Strategies */
        get: operations["strategies_strategies_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/risk": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Risk */
        get: operations["risk_risk_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/system/events": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** System Events */
        get: operations["system_events_system_events_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/system/reconciliations": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Reconciliations */
        get: operations["reconciliations_system_reconciliations_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/commands": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Commands */
        get: operations["commands_commands_get"];
        put?: never;
        /**
         * Submit
         * @description Record the command and return at once: 202, status PENDING. The outcome is read from
         *     `GET /commands/{id}` or the stream — never assumed.
         */
        post: operations["submit_commands_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/commands/{command_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Command */
        get: operations["command_commands__command_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/stream": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Stream */
        get: operations["stream_stream_get"];
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
    schemas: {
        /** CommandDetailDto */
        CommandDetailDto: {
            command: components["schemas"]["CommandDto"];
            /** Results */
            results: components["schemas"]["CommandResultDto"][];
        };
        /** CommandDto */
        CommandDto: {
            /** Id */
            id: string;
            /** Idempotency Key */
            idempotency_key: string;
            /** Type */
            type: string;
            /** Status */
            status: string;
            /** Params */
            params: Record<string, unknown>;
            /** Issued By */
            issued_by: string;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Updated At */
            updated_at: string | null;
            /** Expires At */
            expires_at: string | null;
            /** Reason */
            reason: string;
            /** Attempts */
            attempts: number;
        };
        /** CommandResultDto */
        CommandResultDto: {
            /**
             * Ts
             * Format: date-time
             */
            ts: string;
            /** Status */
            status: string | null;
            /** Message */
            message: string;
            /** Data */
            data: Record<string, unknown>;
        };
        /** ExecutionDto */
        ExecutionDto: {
            /** Id */
            id: string;
            /** Broker Trade Id */
            broker_trade_id: string;
            /** Order Id */
            order_id: string;
            /** Instrument Id */
            instrument_id: string;
            /** Side */
            side: string;
            /** Quantity */
            quantity: number;
            /** Price */
            price: string;
            /** Fees */
            fees: string | null;
            /**
             * Ts
             * Format: date-time
             */
            ts: string;
        };
        /** GateDto */
        GateDto: {
            /** Name */
            name: string;
            /**
             * Outcome
             * @enum {string}
             */
            outcome: "pass" | "fail" | "unknown";
            /** Detail */
            detail: string;
        };
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /** HealthDto */
        HealthDto: {
            /** Status */
            status: string;
            /**
             * Time
             * Format: date-time
             */
            time: string;
        };
        /** KillSwitchDto */
        KillSwitchDto: {
            /** Halted */
            halted: boolean;
            /**
             * Reason
             * @default
             */
            reason: string;
            /** Set By */
            set_by?: string | null;
            /** Changed At */
            changed_at?: string | null;
        };
        /** LoginRequest */
        LoginRequest: {
            /** Passcode */
            passcode: string;
        };
        /** OrderDetailDto */
        OrderDetailDto: {
            order: components["schemas"]["OrderDto"];
            /** Events */
            events: components["schemas"]["OrderEventDto"][];
        };
        /** OrderDto */
        OrderDto: {
            /** Id */
            id: string;
            /** Ordertag */
            ordertag: string;
            /** Instrument Id */
            instrument_id: string;
            /** Side */
            side: string;
            /** Order Type */
            order_type: string;
            /** Quantity */
            quantity: number;
            /** Filled Quantity */
            filled_quantity: number;
            /** Limit Price */
            limit_price: string;
            /** Average Price */
            average_price: string | null;
            /** State */
            state: string;
            /** Strategy Run Id */
            strategy_run_id: string | null;
            /** Signal Id */
            signal_id: string | null;
            /** Parent Order Id */
            parent_order_id: string | null;
            /** Reprice Count */
            reprice_count: number;
            /** Status Message */
            status_message: string;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
        };
        /** OrderEventDto */
        OrderEventDto: {
            /** Seq */
            seq: number;
            /**
             * Ts
             * Format: date-time
             */
            ts: string;
            /** State */
            state: string;
            /** Filled Quantity */
            filled_quantity: number | null;
            /** Reason */
            reason: string;
        };
        /** OverviewDto */
        OverviewDto: {
            /** Session State */
            session_state: string | null;
            /** Session State At */
            session_state_at: string | null;
            kill_switch: components["schemas"]["KillSwitchDto"] | null;
            reconciliation: components["schemas"]["ReconciliationDto"] | null;
            /** Open Positions */
            open_positions: number;
            /** Realised Pnl */
            realised_pnl: string | null;
            /** Unrealised Pnl */
            unrealised_pnl: string | null;
            /** Fees */
            fees: string | null;
            /** Trades */
            trades: number | null;
            /** Snapshot At */
            snapshot_at: string | null;
            /** Pending Commands */
            pending_commands: number;
            /** Trading Mode */
            trading_mode: string | null;
            /** Broker Healthy */
            broker_healthy: boolean | null;
            /** Feed Healthy */
            feed_healthy: boolean | null;
            /** Worker Healthy */
            worker_healthy: boolean | null;
        };
        /** PositionDto */
        PositionDto: {
            /** Instrument Id */
            instrument_id: string;
            /** Net Quantity */
            net_quantity: number;
            /** Average Price */
            average_price: string;
            /** Realised Pnl */
            realised_pnl: string;
            /** Fees */
            fees: string | null;
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
        };
        /** ReconciliationDto */
        ReconciliationDto: {
            /** Id */
            id: string;
            /**
             * Ts
             * Format: date-time
             */
            ts: string;
            /** Status */
            status: string;
            /** Trigger */
            trigger?: string | null;
            /**
             * Discrepancies
             * @default []
             */
            discrepancies: {
                [key: string]: string;
            }[];
            /**
             * Healed
             * @default []
             */
            healed: {
                [key: string]: string;
            }[];
        };
        /** RiskDto */
        RiskDto: {
            /** Limits */
            limits: {
                [key: string]: string;
            };
            /** Recent Rejections */
            recent_rejections: components["schemas"]["RiskEventDto"][];
        };
        /** RiskEventDto */
        RiskEventDto: {
            /** Id */
            id: string;
            /**
             * Ts
             * Format: date-time
             */
            ts: string;
            /** Rule */
            rule: string;
            /** Reason */
            reason: string;
            /** Instrument Id */
            instrument_id: string | null;
            /** Strategy Run Id */
            strategy_run_id: string | null;
            /** Signal Id */
            signal_id: string | null;
        };
        /** StrategyDto */
        StrategyDto: {
            /** Name */
            name: string;
            /** Config Hash */
            config_hash: string | null;
            /** Last Run Id */
            last_run_id: string | null;
            /** Last Run Date */
            last_run_date: string | null;
            /** Signals Last Run */
            signals_last_run: number;
            /** Enabled */
            enabled: boolean;
            /**
             * Status
             * @enum {string}
             */
            status: "running" | "stopped";
            /**
             * Standing
             * @enum {string}
             */
            standing: "validated" | "inconclusive" | "rejected" | "stale" | "none";
            verdict: components["schemas"]["VerdictDto"] | null;
        };
        /**
         * SubmitCommandRequest
         * @description `idempotency_key` is client-generated: the same key can never execute twice.
         */
        SubmitCommandRequest: {
            /** Idempotency Key */
            idempotency_key: string;
            /** Type */
            type: string;
            /** Params */
            params?: Record<string, unknown>;
        };
        /** SystemEventDto */
        SystemEventDto: {
            /** Id */
            id: string;
            /** Type */
            type: string;
            /**
             * Ts
             * Format: date-time
             */
            ts: string;
            /** Data */
            data: Record<string, unknown>;
        };
        /** TokenResponse */
        TokenResponse: {
            /** Token */
            token: string;
            /**
             * Expires At
             * Format: date-time
             */
            expires_at: string;
        };
        /** ValidationError */
        ValidationError: {
            /** Location */
            loc: (string | number)[];
            /** Message */
            msg: string;
            /** Error Type */
            type: string;
        };
        /**
         * VerdictDto
         * @description What a curation concluded, as recorded by the curation run itself.
         */
        VerdictDto: {
            /**
             * Outcome
             * @enum {string}
             */
            outcome: "validated" | "inconclusive" | "rejected";
            /**
             * Recorded At
             * Format: date-time
             */
            recorded_at: string;
            /** Capital */
            capital: string;
            /** First Day */
            first_day: string;
            /** Last Day */
            last_day: string;
            /** Experiment */
            experiment: string;
            /** Source */
            source: string;
            /** Gates */
            gates: components["schemas"]["GateDto"][];
            /** Notes */
            notes: string[];
        };
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    health_health_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HealthDto"];
                };
            };
        };
    };
    login_auth_login_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["LoginRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TokenResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    overview_overview_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["OverviewDto"];
                };
            };
        };
    };
    positions_positions_get: {
        parameters: {
            query?: {
                open_only?: boolean;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PositionDto"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    orders_orders_get: {
        parameters: {
            query?: {
                state?: string | null;
                instrument_id?: string | null;
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["OrderDto"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    order_orders__order_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                order_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["OrderDetailDto"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    executions_executions_get: {
        parameters: {
            query?: {
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ExecutionDto"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    strategies_strategies_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["StrategyDto"][];
                };
            };
        };
    };
    risk_risk_get: {
        parameters: {
            query?: {
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RiskDto"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    system_events_system_events_get: {
        parameters: {
            query?: {
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SystemEventDto"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    reconciliations_system_reconciliations_get: {
        parameters: {
            query?: {
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReconciliationDto"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    commands_commands_get: {
        parameters: {
            query?: {
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CommandDto"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    submit_commands_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SubmitCommandRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CommandDto"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    command_commands__command_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                command_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CommandDetailDto"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    stream_stream_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
        };
    };
}
