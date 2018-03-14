import copy
import inspect
import uuid
from datetime import timedelta
from weakref import WeakValueDictionary

import prefect
from prefect.context import Context
from prefect.utilities.serializers import Serializable
from prefect.utilities.ids import generate_uuid

TASK_REGISTRY = WeakValueDictionary()


class Task(Serializable):

    def __init__(
            self,
            name=None,
            id=None,
            description=None,
            max_retries=0,
            retry_delay=timedelta(minutes=1),
            timeout=None,
            trigger=None,
            secrets=None):
        self.id = generate_uuid()
        self.name = name or type(self).__name__
        self.description = description

        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.timeout = timeout

        if trigger is None:
            trigger = prefect.triggers.all_successful
        self.trigger = trigger

        self.secrets = secrets or {}

        self.register()
        self._prefect_version = prefect.__version__

        flow = Context.get('flow')
        if flow:
            flow.add_task(self)

    def __repr__(self):
        return '<Task: "{self.name}" type={cls} id={id}>'.format(
            cls=type(self).__name__, self=self, id=self.short_id)

    def __hash__(self):
        return id(self)

    # Identification  ----------------------------------------------------------

    @property
    def id(self):
        return self._id

    @id.setter
    def id(self, value):
        self._id = value
        self.register()

    @property
    def short_id(self):
        return self._id[:8]

    def register(self):
        TASK_REGISTRY[self.id] = self

    def copy(self):
        new = copy.copy(self)
        new.id = generate_uuid()
        new.register()
        return new

    # Run  --------------------------------------------------------------------

    def inputs(self):
        return tuple(inspect.signature(self.run).parameters.keys())

    def run(self):
        """
        The main entrypoint for tasks.

        In addition to running arbitrary functions, tasks can interact with
        Prefect in a few ways:
            1. Return an optional result. When this function runs successfully,
                the task is considered successful and the result (if any) is
                made available to downstream edges.
            2. Raise an error. Errors are interpreted as failure.
            3. Raise a signal. Signals can include FAIL, SUCCESS, WAIT, etc.
                and indicate that the task should be put in the indicated
                state.
                - FAIL will lead to retries if appropriate
                - WAIT will end execution and skip all downstream tasks with
                    state WAITING_FOR_UPSTREAM (unless appropriate triggers
                    are set). The task can be run again and should check
                    context.is_waiting to see if it was placed in a WAIT.
        """
        raise NotImplementedError()

    # Dependencies -------------------------------------------------------------

    def __call__(self, *args, **kwargs):
        # this will raise an error if callargs weren't all provided
        signature = inspect.signature(self.run)
        callargs = dict(signature.bind(*args, **kwargs).arguments)

        # bind() compresses all variable keyword arguments, so we expand them
        var_kw_arg = prefect.utilities.functions.get_var_kw_arg(self.run)
        callargs.update(callargs.pop(var_kw_arg, {}))

        flow = Context.get('flow')
        if flow is None:
            flow = prefect.flow.Flow()
        return flow.set_dependencies(task=self, keyword_results=callargs)

    # Operators ----------------------------------------------------------------

    # Serialization ------------------------------------------------------------

    def serialize(self):

        serialized = super().serialize()

        serialized.update(
            name=self.name,
            id=self.id,
            description=self.description,
            max_retries=self.max_retries,
            retry_delay=self.retry_delay,
            timeout=self.timeout,
            trigger=self.trigger,
            type=type(self).__name__,
            prefect_version=self._prefect_version)

        return serialized

    def after_deserialize(self, serialized):
        self.id = serialized['id']
        self.register()

    def __setstate__(self, state):
        self.__dict__.update(state)
        self.register()


class Parameter(Task):
    """
    A Parameter is a special task that defines a required flow input.
    """

    def __init__(self, name, default=None, required=True):
        """
        Args:
            name (str): the Parameter name.

            required (bool): If True, the Parameter is required and the default
                value is ignored.

            default (any): A default value for the parameter. If the default
                is not None, the Parameter will not be required.
        """
        if default is not None:
            required = False

        self.required = required
        self.default = default

        super().__init__(name=name)

    def run(self):
        params = Context.get('parameters', {})
        if self.required and self.name not in params:
            raise prefect.signals.FAIL(
                'Parameter "{}" was required but not provided.'.format(
                    self.name))
        return params.get(self.name, self.default)