import logging

import services.developer_api_webhook_service as webhook_service
from db.database import get_connection

logger = logging.getLogger(__name__)

OPPORTUNITY_WEBHOOK_EVENTS = {
    "opportunity_scan.completed",
    "opportunity_scan.failed",
}


def install() -> None:
    webhook_service.SUPPORTED_WEBHOOK_EVENTS.update(OPPORTUNITY_WEBHOOK_EVENTS)
    logger.info("OPPORTUNITY_SCAN_WEBHOOK_EVENTS_INSTALLED")


def ensure_opportunity_webhook_trigger() -> None:
    webhook_service.ensure_api_webhook_tables()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            CREATE OR REPLACE FUNCTION enqueue_deepalpha_webhook_delivery()
            RETURNS trigger AS $$
            DECLARE
                v_event TEXT;
                v_hook RECORD;
                v_delivery_id TEXT;
                v_reservation_status TEXT;
                v_payload JSONB;
            BEGIN
                IF NEW.job_type NOT IN ('quick_analysis', 'opportunity_scan')
                   OR NEW.status NOT IN ('success', 'error')
                   OR OLD.status IS NOT DISTINCT FROM NEW.status THEN
                    RETURN NEW;
                END IF;

                v_event := CASE
                    WHEN NEW.job_type='quick_analysis' AND NEW.status='success' THEN 'analysis.completed'
                    WHEN NEW.job_type='quick_analysis' AND NEW.status='error' THEN 'analysis.failed'
                    WHEN NEW.job_type='opportunity_scan' AND NEW.status='success' THEN 'opportunity_scan.completed'
                    ELSE 'opportunity_scan.failed'
                END;

                SELECT status INTO v_reservation_status
                FROM api_credit_reservations
                WHERE job_id=NEW.job_id
                LIMIT 1;

                FOR v_hook IN
                    SELECT id, client_id
                    FROM api_webhooks
                    WHERE client_id=NEW.client_id
                      AND status='active'
                      AND v_event = ANY(string_to_array(events, ','))
                LOOP
                    v_delivery_id := 'delivery_' || md5(
                        random()::text || clock_timestamp()::text || v_hook.id::text || NEW.job_id
                    );
                    v_payload := jsonb_build_object(
                        'event', v_event,
                        'delivery_id', v_delivery_id,
                        'created_at', to_char(timezone('UTC', NOW()), 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'),
                        'data', jsonb_build_object(
                            'job_id', NEW.job_id,
                            'status', NEW.status,
                            'job_type', NEW.job_type,
                            'analysis_type', CASE WHEN NEW.job_type='quick_analysis' THEN 'quick' ELSE NULL END,
                            'scan_type', CASE WHEN NEW.job_type='opportunity_scan' THEN 'opportunity_scan' ELSE NULL END,
                            'result', CASE
                                WHEN NEW.status='success' THEN COALESCE(NEW.result_json::jsonb, '{}'::jsonb)
                                ELSE NULL
                            END,
                            'error', CASE
                                WHEN NEW.status='error' THEN COALESCE(
                                    NEW.error,
                                    CASE
                                        WHEN NEW.job_type='opportunity_scan' THEN 'opportunity_scan_failed'
                                        ELSE 'analysis_failed'
                                    END
                                )
                                ELSE NULL
                            END,
                            'credits', jsonb_build_object(
                                'reserved', NEW.units_reserved,
                                'charged', NEW.units_charged,
                                'reservation_status', v_reservation_status
                            )
                        )
                    );
                    INSERT INTO api_webhook_deliveries (
                        delivery_id, webhook_id, client_id, job_id, event, payload_json
                    ) VALUES (
                        v_delivery_id, v_hook.id, NEW.client_id, NEW.job_id, v_event, v_payload::text
                    ) ON CONFLICT (webhook_id, job_id, event) DO NOTHING;
                END LOOP;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
