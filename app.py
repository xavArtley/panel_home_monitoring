import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import panel as pn
import param
import requests
from bokeh.application.application import SessionContext
from bokeh.models import ColumnDataSource
from bokeh.palettes import Category10
from bokeh.plotting import figure
from firebase_admin import credentials, db, initialize_app

from sensor_widget import HumidityWidget, TemperatureWidget
from single_global_task_scheduler import SingleGlobalTaskRunner

pn.extension(design="material")
if pn.state.curdoc is not None:
    pn.state.curdoc.theme = "caliber"
tools = "pan,box_zoom,wheel_zoom,reset"
local_tz = ZoneInfo("Europe/Paris")
palette = Category10[10]

secret_file = (
    Path("/etc/secrets/dht22records-7d6a2f605770.json")
    if Path("/etc/secrets/dht22records-7d6a2f605770.json").exists()
    else next(Path("./api_key/").glob("dht22records-*.json"))
)

empty_records = {"temperature": [], "humidity": [], "timestamp": []}

class Record(param.Parameterized):
    temperature = param.Number(default=0)
    humidity = param.Number(default=50)
    timestamp = param.String()
    label = param.String()

    def __init__(self, **params):
        if "weather_code" in params:
            weather_code = params.pop("weather_code")
        if "label" in params and params["label"]=="outside_data":
            params["label"] = "Outside"

        if "timestamp" in params and isinstance(params["timestamp"], datetime):
            params["timestamp"] = params["timestamp"].strftime("%H:%M:%S %d/%m/%Y")
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
                pn.rx("<h3 style='color: gray'>{}</h3>").format(self.param.label),
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
        query.start_at(str(start_ts))
    if end_date is not None:
        end_ts = _to_timestamp(end_date)
        query.end_at(str(end_ts))

    df: pd.DataFrame = pd.DataFrame.from_dict(query.get(), orient="index").reset_index(drop=True)
    if not df.empty:
        df["timestamp"] = (
            pd.to_datetime(df["timestamp"].astype(int), unit="s", utc=True)
            .dt.tz_convert(local_tz)
            .dt.tz_localize(None)
        )  # convert to local time and remove local information (bokeh does not handle local time)
        df = df.set_index("timestamp")
        return df.select_dtypes(exclude=object)


def get_outside_record(latitude=48.7, longitude=2.1):

    url = f"https://api.open-meteo.com/v1/forecast?latitude={latitude}&longitude={longitude}&current=temperature_2m,relative_humidity_2m&timezone=auto&forecast_days=1"
    resp = requests.get(url)
    if resp.status_code != 200:
        print(json.loads(resp.content.decode())["reason"])
        return
    data = json.loads(resp.content.decode())
    assert isinstance(pn.state.cache, dict)

    record = {
        "label": "Gif-sur-Yvette",
        "timestamp": datetime.fromisoformat(data["current"]["time"]),
        "temperature": data["current"]["temperature_2m"],
        "humidity": data["current"]["relative_humidity_2m"],
    }
    return record

def update_outside_data_firebase():
    last_outside_data = fetch_data("outside_data", limit_to_last=1)
    if last_outside_data is None:
        return
    start_date = last_outside_data.index[0].tz_localize(local_tz)
    now = datetime.now(tz=local_tz)
    if not (now - start_date > timedelta(minutes=15)):
        print(f"Less than 15 minutes elapsed between last update: ({start_date:%H:%M:%S}) and  now: ({now:%H:%M:%S})")
        return
    else:
        print(f"More than 15 minutes between elapsed last update: ({start_date:%H:%M:%S}) and  now: ({now:%H:%M:%S}) => Update")
    end_date = start_date + timedelta(days=1)
    url = f"https://api.open-meteo.com/v1/forecast?latitude=48.69642424920413&longitude=2.1054503941243166&minutely_15=temperature_2m,relative_humidity_2m,weather_code&timezone=auto&start_date={start_date.strftime('%Y-%m-%d')}&end_date={end_date.strftime('%Y-%m-%d')}"
    df = pd.DataFrame(json.loads(requests.get(url).content.decode())["minutely_15"])
    df["time"] = pd.to_datetime(df.time)
    df = df.set_index("time")
    df = df[(df.index>start_date) & (df.index<datetime.now())]
    if df.empty:
        return
    df.columns = ["temperature", "humidity", "weather_code"]
    df.index = df.index.astype(int)//int(1e9) - 3600*2
    df["timestamp"] = df.index
    external_data = df.to_dict("index")
    db.reference("dht_readings/outside_data").update(external_data)


@pn.cache
def setup():
    print("setup")
    initialise_db()
    SingleGlobalTaskRunner(key="update_outside_data_firebase", worker=update_outside_data_firebase, seconds=60)

def init_plotting(sensors, datetime_range):
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
        data = fetch_data(sensor, start_date=datetime_range[0], end_date=datetime_range[1])
        if data is None:
            cd = ColumnDataSource(empty_records)
        else:
            cd = ColumnDataSource(ColumnDataSource.from_df(data))
        cds[sensor] = cd
        legend_label = sensor if sensor != "outside_data" else "Outside"
        renderers[sensor] = [
            plot_temperature.line(
                x="timestamp", y="temperature", source=cd, color=color, legend_label=legend_label
            ),
            plot_humidity.line(x="timestamp", y="humidity", source=cd, color=color, legend_label=legend_label),
        ]
    plot_temperature.legend.click_policy="hide"
    plot_temperature.legend.orientation="horizontal"
    plot_humidity.legend.click_policy="hide"
    plot_humidity.legend.orientation="horizontal"
    return (
        pn.pane.Bokeh(plot_temperature, sizing_mode="stretch_both"),
        pn.pane.Bokeh(plot_humidity, sizing_mode="stretch_both"),
        cds,
        renderers,
    )


def get_last_records(sensors):
    current_records = {}
    for sensor in sensors:
        current_records[sensor] = dict(
            **fetch_data(sensor, limit_to_last=1).reset_index().iloc[0].to_dict(),
            label=sensor,
        )
    return current_records


setup()
sensors = list(db.reference("dht_readings").get(shallow=True).keys())
now = datetime.now()
datetime_range_selection = pn.widgets.DatetimeRangePicker(value=(now - timedelta(days=2),  now), end=now, visible=False, styles={"user-select": "none"})
mode_selection = pn.widgets.RadioButtonGroup(
    name="Mode", options={"Current": "current", "History": "history"}, button_type="primary", sizing_mode="stretch_width"
)
plot_temperature, plot_humidity, cds, renderers = init_plotting(sensors=sensors, datetime_range=datetime_range_selection.value)
current_records = {sensor: Record(**record) for sensor, record in get_last_records(sensors=sensors).items() if record is not None}

current_records_layout = pn.FlexBox(*[r.layout() for r in current_records.values()])

def update():
    print(f"Update {datetime.now(tz=local_tz):%H:%M:%S}")
    datetime_range_selection.end = datetime.now()
    for sensor in sensors:
        last_records  = get_last_records(list(current_records.keys()))
        for sensor, record  in last_records.items():
            record["timestamp"] = record["timestamp"].strftime("%H:%M:%S %d/%m/%Y")
            record.pop("weather_code", None)
            current_records[sensor].param.update(**record)

update_cb = pn.state.add_periodic_callback(update, 60000, start=True)
def on_session_destroyed(session_context: SessionContext):
    print(f"Session {session_context.id} destroyed, stopping callback")

pn.state.on_session_destroyed(on_session_destroyed)

plots = pn.Column(plot_temperature, plot_humidity)
tabs = pn.Tabs(("Current", current_records_layout), ("History", plots))
tabs.jscallback(args={"w": datetime_range_selection}, active="w.visible = source.active==1")

@pn.depends(datetime_range_selection.param.value, watch=True)
def date_range_change(datetime_range):
    try:
        plots.loading = True
        for sensor in sensors:
            if datetime_range[0] == datetime_range[1]:
                if sensor in cds:
                    cds[sensor].data = {}
            else:
                data = fetch_data(sensor, start_date=datetime_range[0], end_date=datetime_range[1])
                if data is not None:
                    cds[sensor].data = ColumnDataSource.from_df(data)
                else:
                    cds[sensor].data = empty_records
    finally:
        plots.loading = False


template = pn.template.BootstrapTemplate(title="Home Temperature/Humidity Monitoring", header_background="#c01754")
template.sidebar += [datetime_range_selection]
template.main.append(tabs)

template.servable()
