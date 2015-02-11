

# st_isLeft(line geometry, point geometry)
# Returns 1 if the given point is on the left side of the line geometry (given the line digit order
# Returns 0 if the given point is colinear to the line
# Returns -1 if the given point is on the right side of the line).
st_isleft_func = """
DROP FUNCTION IF EXISTS st_isleft(geometry, geometry);
CREATE OR REPLACE FUNCTION st_isleft(line geometry, porig geometry)
  RETURNS integer AS
$BODY$
declare
    pfrom geometry;
    pto geometry;
    pct double precision;
    step double precision;
    res double precision;
begin
    pct := st_line_locate_point(line, porig);
    pfrom := st_line_interpolate_point(line, pct);
    -- case of colinearity
    if st_distance(pfrom, porig) < 0.1 then return 0 ;end if;
    -- step is percentage per meter
    step := 1 / st_length(line);

    if pct > 0.98 then
    -- if point is projected near the last point of the linestring
    pto := pfrom;
    pfrom := st_line_interpolate_point(line, GREATEST(0, pct-step));
    else
        pto := st_line_interpolate_point(line, LEAST(1, pct+step));
    end if;
    -- calculate atan2
    res := sign(atan2(
    (st_x(porig) - st_x(pfrom))*(st_y(pto) - st_y(pfrom)) - (st_x(pto) - st_x(pfrom))*(st_y(porig) - st_y(pfrom)),
    (st_x(pto) - st_x(pfrom))*(st_x(porig) - st_x(pfrom)) + (st_y(pto) - st_y(pfrom))*(st_y(porig) - st_y(pfrom))
    ));
    return -res;
end;
$BODY$
  LANGUAGE plpgsql IMMUTABLE
  COST 100;
"""

# compare dates
date_equality_func = """
CREATE OR REPLACE FUNCTION date_equality(start_day integer,
                                         start_month integer,
                                         end_day integer,
                                         end_month integer,
                                         day integer,
                                         month integer)
RETURNS boolean AS
$$
BEGIN
    if start_month is null
        then return true; end if;
    if start_month < end_month and not (month >= start_month and month <= end_month) then
        -- out of range months
        return false; end if;
    if start_month > end_month and (month < start_month and month > end_month) then
        -- out of range months
        return false; end if;

    if month = start_month then
        if day < start_day then
            return false; end if;
        end if;

    if month = end_month then
        if day > end_day then
            return false; end if;
        end if;
    return true;
END;
$$ LANGUAGE plpgsql
IMMUTABLE
"""

# general array sorting
array_sort = """
CREATE OR REPLACE FUNCTION array_sort (ANYARRAY)
RETURNS ANYARRAY LANGUAGE SQL
AS $$
SELECT array_agg(x ORDER BY x) FROM unnest($1) x;
$$
"""

# get max range inside a series
# 0 is automatically added on the left, 1 is added on the right of the array
get_max_range = """
CREATE OR REPLACE FUNCTION get_max_range(float[])
RETURNS TABLE (start float, stop float) AS $$
DECLARE
previous_location float := 0;
last_location float := 1;
maxsize float := 0;
element float;
BEGIN
foreach element in array $1
LOOP
    if element - previous_location > maxsize then
        maxsize := element - previous_location;
        start := previous_location;
        stop := element;
    end if;
    previous_location := element;
END LOOP;
if 1 - previous_location > maxsize then
    start := previous_location;
    stop := 1;
end if;
return next;
END
$$ LANGUAGE plpgsql;
"""
