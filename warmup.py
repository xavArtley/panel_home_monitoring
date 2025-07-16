import panel as pn
from bokeh.application.application import SessionContext
print("warm up")
def on_session_created(session_context: SessionContext):
    print(f"Session {session_context.id} created")
pn.state.on_session_created(on_session_created)
