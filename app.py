import panel as pn
from bokeh.models import ColumnDataSource, HoverTool
from bokeh.plotting import figure
from zoneinfo import ZoneInfo
from datetime import datetime, timezone
from bokeh.palettes import Category10
from firebase_admin import initialize_app, db, credentials


pn.extension(design="material")
if pn.state.curdoc is not None:
    pn.state.curdoc.theme = "caliber"
tools = "pan,box_zoom,wheel_zoom,reset"
rollover = 10000
local_tz = ZoneInfo("Europe/Paris")


def initialise_db():
    cred = credentials.Certificate("/etc/secrets/dht22records-7d6a2f605770.json")
    initialize_app(
        cred,
        {
            "databaseURL": "https://dht22records-default-rtdb.europe-west1.firebasedatabase.app"
        },
    )


def process_record_timestamp(record):
    ts_int = int(record["timestamp"])
    utc_ts = datetime.fromtimestamp(ts_int, tz=timezone.utc)
    local_datetime = utc_ts.astimezone(local_tz)
    record.update(
        time_str=local_datetime.strftime("%H:%M:%S [%Y-%m-%d]"),
        local_datetime=local_datetime,
    )
    return record


def records_to_dict(records):
    return {key: [d[key] for d in records] for key in records[0]}


@pn.cache
def setup():
    print("setup")
    initialise_db()


setup()


options = list(db.reference("dht_readings").get(shallow=True).keys())
palette = Category10[10]
colors = {opts: palette[idx] for idx, opts in enumerate(options)}

cds = {}
for sensor in options:
    records = [
        process_record_timestamp(r)
        for r in db.reference(f"dht_readings/{sensor}")
        .order_by_key()
        .limit_to_last(rollover)
        .get()
        .values()
    ]
    cd = ColumnDataSource(records_to_dict(records))
    cds[sensor] = cd


def update():
    for sensor in options:
        new_records = list(
            filter(
                lambda r: r["local_datetime"] not in cds[sensor].data["local_datetime"],
                [
                    process_record_timestamp(r)
                    for r in db.reference(f"dht_readings/{sensor}")
                    .order_by_key()
                    .limit_to_last(3)
                    .get()
                    .values()
                ],
            )
        )
        if new_records:
            cds[sensor].stream(records_to_dict(new_records), rollover=rollover)


update_cb = pn.state.add_periodic_callback(update, 10000)


def on_session_destroyed(session_context):
    print("Session closed, stopping callback")
    update_cb.stop()


pn.state.on_session_destroyed(on_session_destroyed)


sensor_selection = pn.widgets.MultiSelect(options=options, name="Sensor")


@pn.depends(sensors=sensor_selection.param.value)
def bokeh_plot(sensors):
    if not sensors:
        return
    plot_temperature = figure(
        sizing_mode="stretch_both",
        x_axis_type="datetime",
        tools=tools,
    )
    plot_temperature.toolbar.logo = None
    plot_temperature.yaxis.axis_label = "Temperature (°C)"
    plot_humidity = figure(
        sizing_mode="stretch_both",
        x_axis_type="datetime",
        x_range=plot_temperature.x_range,
        tools=tools,
    )
    plot_humidity.toolbar.logo = None
    plot_humidity.yaxis.axis_label = "Humidity (%)"
    renderers = []
    for sensor in sensors:
        cd = cds.get(sensor)
        renderers.append(
            plot_temperature.line(
                x="local_datetime", y="temperature", source=cd, color=colors[sensor]
            )
        )
        renderers.append(
            plot_humidity.line(
                x="local_datetime", y="humidity", source=cd, color=colors[sensor]
            )
        )

    hover = HoverTool(
        tooltips=[
            ("Temperature", "@temperature{0.0} (°C)"),
            ("Humidity", "@humidity{0.0} (%)"),
            ("Date", "@time_str"),
        ],
        renderers=renderers,
        mode="vline",
    )
    plot_temperature.add_tools(hover)
    plot_humidity.add_tools(hover)
    return pn.Column(
        pn.pane.Bokeh(plot_temperature, sizing_mode="stretch_both"),
        pn.pane.Bokeh(plot_humidity, sizing_mode="stretch_both"),
    )


template = pn.template.BootstrapTemplate()
template.sidebar.append(sensor_selection)
template.main.append(bokeh_plot)

template.servable()
