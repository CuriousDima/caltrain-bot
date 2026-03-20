DROP TABLE IF EXISTS train_station_journeys;
DROP VIEW IF EXISTS train_stop_timeline;

CREATE VIEW train_stop_timeline AS
WITH RECURSIVE calendar_span (
    feed_id,
    service_id,
    service_date,
    end_date,
    monday,
    tuesday,
    wednesday,
    thursday,
    friday,
    saturday,
    sunday
) AS (
    SELECT
        feed_id,
        service_id,
        start_date,
        end_date,
        monday,
        tuesday,
        wednesday,
        thursday,
        friday,
        saturday,
        sunday
    FROM calendar

    UNION ALL

    SELECT
        feed_id,
        service_id,
        date(service_date, '+1 day'),
        end_date,
        monday,
        tuesday,
        wednesday,
        thursday,
        friday,
        saturday,
        sunday
    FROM calendar_span
    WHERE service_date < end_date
),
base_service_dates AS (
    SELECT
        feed_id,
        service_id,
        service_date
    FROM calendar_span
    WHERE CASE CAST(strftime('%w', service_date) AS INTEGER)
        WHEN 0 THEN sunday
        WHEN 1 THEN monday
        WHEN 2 THEN tuesday
        WHEN 3 THEN wednesday
        WHEN 4 THEN thursday
        WHEN 5 THEN friday
        WHEN 6 THEN saturday
    END = 1
),
service_dates AS (
    SELECT feed_id, service_id, service_date
    FROM base_service_dates

    EXCEPT

    SELECT feed_id, service_id, date
    FROM calendar_dates
    WHERE exception_type = 2

    UNION

    SELECT feed_id, service_id, date
    FROM calendar_dates
    WHERE exception_type = 1
),
stop_lookup AS (
    SELECT
        stops.feed_id,
        stops.stop_id,
        COALESCE(stops.parent_station, stops.stop_id) AS station_id,
        COALESCE(parent_station.stop_name, stops.stop_name) AS station_name,
        TRIM(
            REPLACE(
                REPLACE(
                    REPLACE(
                        LOWER(COALESCE(parent_station.stop_name, stops.stop_name)),
                        ' caltrain station',
                        ''
                    ),
                    ' station',
                    ''
                ),
                ' caltrain',
                ''
            )
        ) AS station_query_name
    FROM stops
    LEFT JOIN stops AS parent_station
        ON parent_station.feed_id = stops.feed_id
       AND parent_station.stop_id = stops.parent_station
),
trip_bounds AS (
    SELECT
        feed_id,
        trip_id,
        MIN(stop_sequence) AS first_stop_sequence,
        MAX(stop_sequence) AS last_stop_sequence
    FROM stop_times
    GROUP BY feed_id, trip_id
),
trip_terminals AS (
    SELECT
        bounds.feed_id,
        bounds.trip_id,
        first_stop.station_id AS trip_origin_station_id,
        first_stop.station_name AS trip_origin_station_name,
        first_stop.station_query_name AS trip_origin_station_query_name,
        last_stop.station_id AS trip_destination_station_id,
        last_stop.station_name AS trip_destination_station_name,
        last_stop.station_query_name AS trip_destination_station_query_name
    FROM trip_bounds AS bounds
    JOIN stop_times AS first_stop_time
        ON first_stop_time.feed_id = bounds.feed_id
       AND first_stop_time.trip_id = bounds.trip_id
       AND first_stop_time.stop_sequence = bounds.first_stop_sequence
    JOIN stop_lookup AS first_stop
        ON first_stop.feed_id = first_stop_time.feed_id
       AND first_stop.stop_id = first_stop_time.stop_id
    JOIN stop_times AS last_stop_time
        ON last_stop_time.feed_id = bounds.feed_id
       AND last_stop_time.trip_id = bounds.trip_id
       AND last_stop_time.stop_sequence = bounds.last_stop_sequence
    JOIN stop_lookup AS last_stop
        ON last_stop.feed_id = last_stop_time.feed_id
       AND last_stop.stop_id = last_stop_time.stop_id
),
stop_event_times AS (
    SELECT
        service_dates.service_date,
        trips.trip_id,
        trips.trip_short_name AS train_number,
        COALESCE(
            routes.route_short_name,
            routes.route_long_name,
            routes.route_id
        ) AS service_pattern,
        trips.trip_headsign,
        trips.direction_id,
        CASE trips.direction_id
            WHEN 0 THEN 'northbound'
            WHEN 1 THEN 'southbound'
        END AS direction_name,
        terminals.trip_origin_station_id,
        terminals.trip_origin_station_name,
        terminals.trip_origin_station_query_name,
        terminals.trip_destination_station_id,
        terminals.trip_destination_station_name,
        terminals.trip_destination_station_query_name,
        stop_times.stop_sequence,
        stop_lookup.station_id,
        stop_lookup.station_name,
        stop_lookup.station_query_name,
        datetime(
            service_dates.service_date || ' 00:00:00',
            printf(
                '+%d seconds',
                CAST(
                    ROUND(
                        (
                            julianday(stop_times.arrival_time)
                            - julianday('1970-01-01 00:00:00')
                        ) * 86400
                    ) AS INTEGER
                )
            )
        ) AS arrival_timestamp,
        datetime(
            service_dates.service_date || ' 00:00:00',
            printf(
                '+%d seconds',
                CAST(
                    ROUND(
                        (
                            julianday(stop_times.departure_time)
                            - julianday('1970-01-01 00:00:00')
                        ) * 86400
                    ) AS INTEGER
                )
            )
        ) AS departure_timestamp
    FROM service_dates
    JOIN trips
        ON trips.feed_id = service_dates.feed_id
       AND trips.service_id = service_dates.service_id
    JOIN routes
        ON routes.feed_id = trips.feed_id
       AND routes.route_id = trips.route_id
    JOIN stop_times
        ON stop_times.feed_id = trips.feed_id
       AND stop_times.trip_id = trips.trip_id
    JOIN stop_lookup
        ON stop_lookup.feed_id = stop_times.feed_id
       AND stop_lookup.stop_id = stop_times.stop_id
    JOIN trip_terminals AS terminals
        ON terminals.feed_id = trips.feed_id
       AND terminals.trip_id = trips.trip_id
),
ranked_stop_events AS (
    SELECT
        stop_event_times.*,
        MIN(departure_timestamp) OVER (
            PARTITION BY service_date, trip_id
        ) AS trip_origin_departure_timestamp,
        MAX(arrival_timestamp) OVER (
            PARTITION BY service_date, trip_id
        ) AS trip_destination_arrival_timestamp,
        CAST(
            (
                unixepoch(
                    MAX(arrival_timestamp) OVER (
                        PARTITION BY service_date, trip_id
                    )
                )
                - unixepoch(
                    MIN(departure_timestamp) OVER (
                        PARTITION BY service_date, trip_id
                    )
                )
            ) / 60 AS INTEGER
        ) AS trip_duration_minutes
    FROM stop_event_times
)
SELECT
    ranked_stop_events.service_date,
    ranked_stop_events.trip_id,
    ranked_stop_events.train_number,
    ranked_stop_events.service_pattern,
    ranked_stop_events.trip_headsign,
    ranked_stop_events.direction_id,
    ranked_stop_events.direction_name,
    ranked_stop_events.trip_origin_station_id,
    ranked_stop_events.trip_origin_station_name,
    ranked_stop_events.trip_origin_station_query_name,
    ranked_stop_events.trip_destination_station_id,
    ranked_stop_events.trip_destination_station_name,
    ranked_stop_events.trip_destination_station_query_name,
    ranked_stop_events.trip_origin_departure_timestamp,
    ranked_stop_events.trip_destination_arrival_timestamp,
    ranked_stop_events.trip_duration_minutes,
    ranked_stop_events.stop_sequence,
    ranked_stop_events.station_id,
    ranked_stop_events.station_name,
    ranked_stop_events.station_query_name,
    ranked_stop_events.arrival_timestamp,
    ranked_stop_events.departure_timestamp,
    unixepoch(ranked_stop_events.arrival_timestamp) AS arrival_unix,
    unixepoch(ranked_stop_events.departure_timestamp) AS departure_unix
FROM ranked_stop_events;

CREATE TABLE train_station_journeys AS
SELECT
    origin_call.service_date,
    origin_call.train_number,
    origin_call.service_pattern,
    origin_call.trip_headsign,
    origin_call.direction_id,
    origin_call.direction_name,
    origin_call.trip_origin_station_id,
    origin_call.trip_origin_station_name,
    origin_call.trip_origin_station_query_name,
    origin_call.trip_destination_station_id,
    origin_call.trip_destination_station_name,
    origin_call.trip_destination_station_query_name,
    origin_call.trip_origin_departure_timestamp,
    origin_call.trip_destination_arrival_timestamp,
    origin_call.trip_duration_minutes,
    origin_call.station_id AS origin_station_id,
    origin_call.station_name AS origin_station_name,
    origin_call.station_query_name AS origin_station_query_name,
    origin_call.departure_timestamp AS origin_departure_timestamp,
    origin_call.departure_unix AS origin_departure_unix,
    destination_call.station_id AS destination_station_id,
    destination_call.station_name AS destination_station_name,
    destination_call.station_query_name AS destination_station_query_name,
    destination_call.arrival_timestamp AS destination_arrival_timestamp,
    destination_call.arrival_unix AS destination_arrival_unix,
    CAST(
        (
            destination_call.arrival_unix
            - origin_call.departure_unix
        ) / 60 AS INTEGER
    ) AS travel_minutes
FROM train_stop_timeline AS origin_call
JOIN train_stop_timeline AS destination_call
    ON destination_call.service_date = origin_call.service_date
   AND destination_call.trip_id = origin_call.trip_id
   AND destination_call.stop_sequence > origin_call.stop_sequence
   AND destination_call.station_id <> origin_call.station_id;
