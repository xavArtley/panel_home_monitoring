import panel as pn
from bokeh.models import ColumnDataSource, HoverTool
from bokeh.plotting import figure
from zoneinfo import ZoneInfo
from datetime import datetime, timezone, date
from bokeh.palettes import Category10
from firebase_admin import initialize_app, db, credentials
from pathlib import Path
import pandas as pd
import param
from sensor_widget import TemperatureWidget, HumidityWidget

pn.extension(design="material")
if pn.state.curdoc is not None:
    pn.state.curdoc.theme = "caliber"
tools = "pan,box_zoom,wheel_zoom,reset"
local_tz = ZoneInfo("Europe/Paris")
palette = Category10[10]

secret_file = (
    Path("/etc/secrets/dht22records-7d6a2f605770.json")
    if Path("/etc/secrets/dht22records-7d6a2f605770.json").exists()
    else Path("./api_key/dht22records-7d6a2f605770.json")
)


class Record(param.Parameterized):
    temperature = param.Number(default=0)
    humidity = param.Number(default=50)
    timestamp = param.String()
    label = param.String()

    def __init__(self, **params):
        if "timestamp" in params and isinstance(params["timestamp"], datetime):
            params["timestamp"] = params["timestamp"].strftime("%H:%M:%S")
        super().__init__(**params)

    def layout(self):
        tw = TemperatureWidget(
            value=self.param.temperature.rx(),
            styles={"font-size": "16px", "margin": "20px 20px 0px 20px"},
        )
        hw = HumidityWidget(
            value=self.param.humidity.rx(),
            styles={"font-size": "16px", "margin": "20px 20px 0px 20px"},
        )
        return pn.WidgetBox(
                pn.rx("### {}").format(self.param.label),
            pn.Row(tw, hw),
            pn.rx("`last_upd: {}`").format(self.param.timestamp),
            styles={
                "border-radius": "12px",
                "box-shadow": "0 4px 10px rgba(0, 0, 0, 0.1)",
            },
            margin=10,
        )


def initialise_db():
    cred = credentials.Certificate(secret_file)
    initialize_app(
        cred,
        {
            "databaseURL": "https://dht22records-default-rtdb.europe-west1.firebasedatabase.app"
        },
    )


@pn.cache
def setup():
    print("setup")
    initialise_db()


setup()


def fetch_data(
    sensor: str,
    limit_to_last: int | None = None,
    start_date: date | datetime | None = None,
    end_date: date | datetime | None = None,
) -> pd.DataFrame | None:

    ref = db.reference(f"dht_readings/{sensor}")
    query = ref.order_by_key()
    if limit_to_last is not None:
        query = query.limit_to_last(limit_to_last)

    def _to_timestamp(date: date | datetime):
        if not isinstance(date, datetime):
            return int(datetime.combine(date, datetime.min.time()).timestamp())
        else:
            return int(date.timestamp())

    if start_date is not None:
        start_ts = _to_timestamp(start_date)
        query.start_at(start_ts)
    if end_date is not None:
        end_ts = _to_timestamp(end_date)
        query.end_at(end_ts)

    df = pd.DataFrame.from_dict(query.get(), orient="index").reset_index(drop=True)
    df["timestamp"] = (
        pd.to_datetime(df["timestamp"].astype(int), unit="s", utc=True)
        .dt.tz_convert(local_tz)
        .dt.tz_localize(None)
    )  # convert to local time and remove local information (bokeh does not handle local time)
    df = df.set_index("timestamp")
    return df.select_dtypes(exclude=object)


def init_plotting(sensors):
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
    cds = {}
    renderers = {}
    for idx, sensor in enumerate(sensors):
        color = palette[idx]
        data = fetch_data(sensor)
        assert data is not None
        cd = ColumnDataSource(ColumnDataSource.from_df(data))
        cds[sensor] = cd
        renderers[sensor] = [
            plot_temperature.line(
                x="timestamp", y="temperature", source=cd, color=color
            ),
            plot_humidity.line(x="timestamp", y="humidity", source=cd, color=color),
        ]
    # hover = HoverTool(
    #     tooltips=[
    #         ("Temperature", "@temperature{0.0} (°C)"),
    #         ("Humidity", "@humidity{0.0} (%)"),
    #         ("Date", "@time_str"),
    #     ],
    #     renderers=renderers,
    #     mode="vline",
    # )
    # plot_temperature.add_tools(hover)
    # plot_humidity.add_tools(hover)

    return (
        pn.pane.Bokeh(plot_temperature, sizing_mode="stretch_both"),
        pn.pane.Bokeh(plot_humidity, sizing_mode="stretch_both"),
        cds,
        renderers,
    )


def init_current_records():
    current_records = {}
    for sensor in sensors:
        current_records[sensor] = Record(
            **fetch_data(sensor, limit_to_last=1).reset_index().iloc[0].to_dict(),
            label=sensor,
        )
    return current_records


sensors = list(db.reference("dht_readings").get(shallow=True).keys())
sensor_selection = pn.widgets.MultiSelect(options=sensors, name="Sensor")
mode_selection = pn.widgets.RadioButtonGroup(
    name="Mode", options={"Current": "current", "History": "history"}, button_type="primary"
)
plot_temperature, plot_humidity, cds, renderers = init_plotting(sensors=sensors)
current_records = init_current_records()


@pn.depends(sensors_selected=sensor_selection.param.value, watch=True)
def set_visible_renderers(sensors_selected):
    for k, (rt, rh) in renderers.items():
        if k in sensors_selected:
            rt.visible = True
            rh.visible = True
        else:
            rt.visible = False
            rh.visible = False


plot_layout = pn.Column(plot_temperature, plot_humidity)
current_records_layout = pn.FlexBox(*[r.layout() for r in current_records.values()])


@pn.depends(mode_selection, watch=True, on_init=True)
def select_view(mode):
    with pn.io.hold():
        if mode == "history":
            plot_layout.visible = True
            current_records_layout.visible = False
            # return plot_layout
        else:
            plot_layout.visible = False
            current_records_layout.visible = True
            # return  current_records_layout
select_view(mode_selection.value)

def update():
    print("Update")
    for sensor in sensors:
        data = fetch_data(sensor, limit_to_last=3)
        if data is None or data.empty:
            continue
        new_data = data[~data.index.isin(cds[sensor].data["timestamp"])]
        if new_data.empty:
            continue
        last_data = new_data.reset_index().iloc[-1]
        last_data["timestamp"] = last_data["timestamp"].strftime("%H:%M:%S")
        last_data = last_data.to_dict()

        current_records[sensor].param.update(**last_data)
        cds[sensor].stream(ColumnDataSource.from_df(new_data))


update_cb = pn.state.add_periodic_callback(update, 10000)


def on_session_destroyed(session_context):
    print("Session closed, stopping callback")
    update_cb.stop()


pn.state.on_session_destroyed(on_session_destroyed)


template = pn.template.BootstrapTemplate()
template.sidebar += [sensor_selection, mode_selection]
template.main.append(pn.Column(plot_layout, current_records_layout))

template.servable()
