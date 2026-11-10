-- NetDiagnose AI — analysis queries (Week 4)
-- Run any of these in psql or pgAdmin against the netdiagnose database.

-- 1. Anomalies per device per hour (Week 4 exit gate)
SELECT d.hostname,
       date_trunc('hour', a.detected_at)        AS hour,
       count(*) FILTER (WHERE a.is_anomaly)     AS anomalies,
       count(*)                                 AS scored
FROM anomalies a
JOIN devices d ON d.id = a.device_id
GROUP BY d.hostname, hour
ORDER BY hour DESC, anomalies DESC;

-- 2. Top devices by anomaly count in the last 24 hours
SELECT d.hostname, d.device_type, count(*) AS anomalies, round(avg(a.score)::numeric, 3) AS avg_score
FROM anomalies a
JOIN devices d ON d.id = a.device_id
WHERE a.is_anomaly AND a.detected_at >= now() - interval '24 hours'
GROUP BY d.hostname, d.device_type
ORDER BY anomalies DESC;

-- 3. Detector quality on stored data (dataset rows carry the true label)
SELECT count(*) FILTER (WHERE a.is_anomaly AND m.true_label = 1)     AS true_positives,
       count(*) FILTER (WHERE a.is_anomaly AND m.true_label = 0)     AS false_positives,
       count(*) FILTER (WHERE NOT a.is_anomaly AND m.true_label = 1) AS false_negatives,
       count(*) FILTER (WHERE NOT a.is_anomaly AND m.true_label = 0) AS true_negatives,
       round(100.0 * count(*) FILTER (WHERE a.is_anomaly AND m.true_label = 1)
             / NULLIF(count(*) FILTER (WHERE a.is_anomaly), 0), 1)   AS precision_pct
FROM anomalies a
JOIN network_metrics m ON m.id = a.metric_id
WHERE m.true_label IS NOT NULL;

-- 4. Hourly averages for one device (for dashboard charts)
SELECT date_trunc('hour', ts)              AS hour,
       round(avg(latency_ms)::numeric, 1)  AS avg_latency_ms,
       round(avg(packet_loss_pct)::numeric, 2) AS avg_loss_pct,
       round(avg(throughput_mbps)::numeric, 2) AS avg_throughput_mbps,
       round(avg(cpu_usage_pct)::numeric, 1)   AS avg_cpu_pct,
       count(*)                            AS samples
FROM network_metrics
WHERE device_id = (SELECT id FROM devices WHERE hostname = 'web-srv-01')
  AND ts >= now() - interval '24 hours'
GROUP BY hour
ORDER BY hour;

-- 5. Metric profile of anomalous vs normal rows (what moves when the detector fires?)
SELECT a.is_anomaly,
       count(*)                                         AS n,
       round(avg(m.latency_ms)::numeric, 1)             AS avg_latency_ms,
       round(avg(m.packet_loss_pct)::numeric, 2)        AS avg_loss_pct,
       round(avg(m.jitter_ms)::numeric, 1)              AS avg_jitter_ms,
       round(avg(m.throughput_mbps)::numeric, 2)        AS avg_throughput_mbps,
       round(avg(m.interface_utilization_pct)::numeric, 2) AS avg_util_pct
FROM anomalies a
JOIN network_metrics m ON m.id = a.metric_id
GROUP BY a.is_anomaly;

-- 6. Share of flows flagged per protocol (reads the model features stored as JSONB)
SELECT m.features ->> 'proto'                          AS proto,
       count(*)                                        AS flows,
       count(*) FILTER (WHERE a.is_anomaly)            AS flagged,
       round(100.0 * count(*) FILTER (WHERE a.is_anomaly) / count(*), 1) AS flagged_pct
FROM anomalies a
JOIN network_metrics m ON m.id = a.metric_id
GROUP BY proto
HAVING count(*) >= 100
ORDER BY flows DESC
LIMIT 15;

-- 7. Busiest hours across the network (anomaly bursts worth turning into incidents)
SELECT date_trunc('hour', detected_at) AS hour,
       count(*) FILTER (WHERE is_anomaly) AS anomalies,
       count(DISTINCT device_id) FILTER (WHERE is_anomaly) AS devices_affected
FROM anomalies
GROUP BY hour
ORDER BY anomalies DESC
LIMIT 10;

-- 8. Rolling 3-hour anomaly count per device (window function)
SELECT hostname, hour, anomalies,
       sum(anomalies) OVER (PARTITION BY hostname ORDER BY hour
                            ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) AS rolling_3h
FROM (
    SELECT d.hostname, date_trunc('hour', a.detected_at) AS hour,
           count(*) FILTER (WHERE a.is_anomaly) AS anomalies
    FROM anomalies a
    JOIN devices d ON d.id = a.device_id
    GROUP BY d.hostname, hour
) per_hour
ORDER BY hostname, hour;

-- 9. Active model and its recorded test metrics
SELECT version, threshold, metrics -> 'test' AS test_metrics, trained_at
FROM model_registry
WHERE is_active;
