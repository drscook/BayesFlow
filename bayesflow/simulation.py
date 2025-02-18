# Copyright (c) 2022 The BayesFlow Developers

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import numpy as np
import logging
import pickle
import os
import matplotlib.pyplot as plt
from tqdm.autonotebook import tqdm
logging.basicConfig()

from bayesflow.default_settings import DEFAULT_KEYS
from bayesflow.exceptions import ConfigurationError
from bayesflow.diagnostics import plot_prior2d


class ContextGenerator:
    """Basic interface for a simulation module responsible for generating variables over which
    we want to amortize during simulation-based training, but do not want to perform inference on.
    Both priors and simulators in a generative framework can have their own context generators,
    depending on the particular modeling goals.

    The interface distinguishes between two types of context: batchable and non-batchable.

    - Batchable context variables differ for each simulation in each training batch
    - Non-batchable context varibales stay the same for each simulation in a batch, but differ across batches

    Examples for batchable context variables include experimental design variables, design matrices, etc.
    Examples for non-batchable context variables include the number of observations in an experiment, positional
    encodings, time indices, etc.

    While the latter can also be considered batchable in principle, batching them would require non-Tensor
    (i.e., non-rectangular) data structures, which usually means inefficient computations.  

    Example for a simulation context which will generate a random number of observations between 1 and 100 for 
    each training batch:

    >>> gen = ContextGenerator(non_batchable_context_fun=lambda : np.random.randint(1, 101))
    """

    def __init__(self, batchable_context_fun : callable = None, non_batchable_context_fun: callable = None,
                 use_non_batchable_for_batchable: bool = False):
        """Instantiates a context generator responsible for random generation of variables which vary from data set
        to data set but cannot be considered data or parameters, e.g., time indices, number of observations, etc.
        A batchable, non-batchable, or both context functions should be provided to the constructor. An optional
        argument dictates whether the outputs of the non-batchable context function should be used as inputs to
        batchable context.

        Parameters
        ----------
        batchable_context_fun             : callable
            A function with optional control arguments responsible for generating per-simulation set context variables
        non_batchable_context_fun         : callable 
            A function with optional control arguments responsible for generating per-batch-of-simulations context variables.
        use_non_batchable_for_batchable   : bool, optional, default: False
            Determines whether to use output of non_batchable_context_fun as input to batchable_context_fun. Only relevant
            when both context types are provided.
        """

        self.batchable_context_fun = batchable_context_fun
        self.non_batchable_context_fun = non_batchable_context_fun
        self.use_non_batchable_for_batchable = use_non_batchable_for_batchable

    def __call__(self, batch_size, *args, **kwargs):
        """ Wraps the method generate_context, which returns a dictionary with 
        batchable and non batchable context.

        Optional positional and keyword arguments are passed to the internal 
        context-generating functions or ignored if the latter are None.

        Parameters
        ----------

        batch_size : int
            The batch_size argument used for batchable context.

        Returns
        -------

        context_dict : dictionary
            A dictionary with context variables with the following keys:
            `batchable_context` : value
            `non_batchable_context` : value
        
        Note, that the values of the context variables will be None, if the
        corresponding context-generating functions have not been provided when
        initializing this object.
        """

        return self.generate_context(batch_size, *args, **kwargs)

    def batchable_context(self, batch_size, *args, **kwargs):
        """ Generates 'batch_size' context variables given optional arguments. 
        Return type is a list of context variables.
        """
        if self.batchable_context_fun is not None:
            context = [self.batchable_context_fun(*args, **kwargs) for _ in range(batch_size)]
            return context
        return None

    def non_batchable_context(self, *args, **kwargs):
        """ Generates a context variable shared across simulations in a given batch, given optional arguments.
        """
        if self.non_batchable_context_fun is not None:
            return self.non_batchable_context_fun(*args, **kwargs)
        return None

    def generate_context(self, batch_size, *args, **kwargs):
        """Creates a dictionary with batchable and non batchable context.

         Parameters
        ----------

        batch_size   : int
            The batch_size argument used for batchable context.

        Returns
        -------
        context_dict : dictionary
            A dictionary with context variables with the following keys, if default keys not changed:
            ``batchable_context`` : value
            ``non_batchable_context`` : value
        
        Note, that the values of the context variables will be ``None``, if the
        corresponding context-generating functions have not been provided when
        initializing this object.
        """

        out_dict = {}
        out_dict[DEFAULT_KEYS['non_batchable_context']] = self.non_batchable_context()
        if self.use_non_batchable_for_batchable:
            out_dict[DEFAULT_KEYS['batchable_context']] = self.batchable_context(batch_size, 
            out_dict[DEFAULT_KEYS['non_batchable_context']], *args, **kwargs)
        else:
            out_dict[DEFAULT_KEYS['batchable_context']] = self.batchable_context(batch_size, *args, **kwargs)
        return out_dict


class Prior:
    """ Basic interface for a simulation module responsible for generating random draws from a 
    prior distribution.

    The prior functions should return a np.array of simulation parameters which will be internally used
    by the GenerativeModel interface for simulations.
   
    An optional context generator (i.e., an instance of ContextGenerator) or a user-defined callable object 
    implementing the following two methods can be provided:
    - context_generator.batchable_context(batch_size)
    - context_generator.non_batchable_context()
    """

    def __init__(self, batch_prior_fun : callable = None, prior_fun : callable = None, 
                 context_generator : callable = None, param_names : list = None):
        """
        Instantiates a prior generator which will draw random parameter configurations from a user-informed prior
        distribution. No improper priors are allowed, as these may render the generative scope of a model undefined.
        
        Parameters
        ----------
        batch_prior_fun     : callable
            A function (callbale object) with optional control arguments responsible for generating batches 
            of per-simulation parameters.
        prior_fun           : callable
            A function (callbale object) with optional control arguments responsible for generating 
            per-simulation parameters.
        context generator   : callable, optional, (default None, recommended instance of ContextGenerator)
            An optional function (ideally an instance of ContextGenerator) for generating prior context variables.
        param_names         : list of str, optional, (default None)
            A list with strings representing the names of the parameters.
        """

        if (batch_prior_fun is None) is (prior_fun is None):
            raise ConfigurationError('Either batch_prior_fun or prior_fun should be provided, but not both!')
        self.prior = prior_fun
        self.batched_prior = batch_prior_fun
        self.context_gen = context_generator
        self.param_names = param_names
        if prior_fun is None:
            self.is_batched = True
        else:
            self.is_batched = False

    def __call__(self, batch_size, *args, **kwargs):
        """ Generates `batch_size` draws from the prior given optional context generator.

        Parameters
        ----------
        batch_size : int
            The number of draws to obtain from the prior + context generator functions.
        *args      : tuple
            Optional positional arguments passed to the generator functions.
        **kwargs   : dict
            Optional keyword arguments passed to the generator functions.

        Returns
        -------
        out_dict - a dictionary with the quantities generated from the prior + context funcitons.
        """

        # Prepare placeholder output dictionary
        out_dict = {
            DEFAULT_KEYS['prior_draws']: None,
            DEFAULT_KEYS['batchable_context'] : None,
            DEFAULT_KEYS['non_batchable_context'] : None
        }

        # Populate dictionary with context or leave at None
        if self.context_gen is not None:
            context_dict = self.context_gen(batch_size, *args, **kwargs)
            out_dict[DEFAULT_KEYS['non_batchable_context']] = context_dict['non_batchable_context']
            out_dict[DEFAULT_KEYS['batchable_context']] = context_dict[DEFAULT_KEYS['batchable_context']]

        # Generate prior draws according to context:
        # No context type
        if out_dict[DEFAULT_KEYS['batchable_context']] is None and out_dict[DEFAULT_KEYS['non_batchable_context']] is None:
            if self.is_batched:
                out_dict[DEFAULT_KEYS['prior_draws']] = np.array(self.batched_prior(batch_size=batch_size, *args, **kwargs))
            else:
                out_dict[DEFAULT_KEYS['prior_draws']] = np.array([self.prior(*args, **kwargs) for _ in range(batch_size)])
        
        # Only batchable context
        elif out_dict[DEFAULT_KEYS['non_batchable_context']] is None:
            if self.is_batched:
                out_dict[DEFAULT_KEYS['prior_draws']] = np.array(self.batched_prior(out_dict[DEFAULT_KEYS['batchable_context']], batch_size=batch_size, *args, **kwargs))
            else: 
                out_dict[DEFAULT_KEYS['prior_draws']] = np.array([self.prior(out_dict[DEFAULT_KEYS['batchable_context']][b], *args, **kwargs) 
                for b in range(batch_size)])
            
        # Only non-batchable context
        elif out_dict[DEFAULT_KEYS['batchable_context']] is None:
            if self.is_batched:
                out_dict[DEFAULT_KEYS['prior_draws']] = np.array(self.batched_prior(out_dict[DEFAULT_KEYS['non_batchable_context']], batch_size=batch_size))
            else:
                out_dict[DEFAULT_KEYS['prior_draws']] = np.array([self.prior(out_dict[DEFAULT_KEYS['non_batchable_context']], *args, **kwargs) 
                for _ in range(batch_size)])

        # Both batchable and non_batchable context
        else:
            if self.is_batched:
                out_dict[DEFAULT_KEYS['prior_draws']] = np.array(
                    self.batched_prior(out_dict[DEFAULT_KEYS['batchable_context']], 
                               out_dict[DEFAULT_KEYS['non_batchable_context']], batch_size=batch_size, *args, **kwargs))
            else:    
                out_dict[DEFAULT_KEYS['prior_draws']] = np.array([
                    self.prior(out_dict[DEFAULT_KEYS['batchable_context']][b], 
                               out_dict[DEFAULT_KEYS['non_batchable_context']], *args, **kwargs) 
                    for b in range(batch_size)])

        return out_dict

    def plot_prior2d(self, **kwargs):
        """ Generates a 2D plot representing bivariate prior ditributions. Uses the function
        `bayesflow.diagnostics.plot_prior2d() internally for generating the plot.

        Parameters
        ----------
        **kwargs : dict
            Optional keyword arguments passed to the `plot_prior2d` function.

        Returns
        -------
        f : plt.Figure - the figure instance for optional saving
        """

        return plot_prior2d(self, param_names=self.param_names, **kwargs)

    def estimate_means_and_stds(self, n_draws=1000, *args, **kwargs):
        """ Estimates prior means and stds given n_draws from the prior, useful
        for z-standardization of the prior draws.
        
        Parameters
        ----------

        n_draws: int, optional (default = 1000)
            The number of random draws to obtain from the joint prior.
        *args      : tuple
            Optional positional arguments passed to the generator functions.
        **kwargs   : dict
            Optional keyword arguments passed to the generator functions.

        Returns
        -------    
        (prior_means, prior_stds) - tuple of np.ndarrays
            The estimated means and stds of the joint prior.
        """

        out_dict = self(n_draws, *args, **kwargs)
        prior_means = np.mean(out_dict[DEFAULT_KEYS['prior_draws']], axis=0, keepdims=True)
        prior_stds = np.std(out_dict[DEFAULT_KEYS['prior_draws']], axis=0, ddof=1, keepdims=True)
        return prior_means, prior_stds

    def logpdf(self, prior_draws):
        raise NotImplementedError('Prior density computation is under construction!')
            

class Simulator:
    """ Basic interface for a simulation module responsible for generating randomized simulations given a prior
    parameter distribution and optional context variables, given a user-provided simulation function.

    The user-provided simulator functions should return a np.array of synthetic data which will be used internally
    by the GenerativeModel interface for simulations.
   
    An optional context generator (i.e., an instance of ContextGenerator) or a user-defined callable object 
    implementing the following two methods can be provided:
    - context_generator.batchable_context(batch_size)
    - context_generator.non_batchable_context()
    """

    def __init__(self, batch_simulator_fun=None, simulator_fun=None, context_generator=None):
        """ Instantiates a data generator which will perform randomized simulations given a set of parameters and optional context.
        Either a batch_simulator_fun or simulator_fun, but not both, should be provided to instantiate a Simulator object.

        If a batch_simulator_fun is provided, the interface will assume that the function operates on batches of parameter
        vectors and context variables and will pass the latter directly to the function. Power users should attempt to provide
        optimized batched simulators. 

        If a simulator_fun is provided, the interface will assume thatthe function operates on single parameter vectors and
        context variables and will wrap the simulator internally to allow batched functionality.
        
        Parameters
        ----------
        batch_simulator_fun  : callable
            A function (callbale object) with optional control arguments responsible for generating a batch of simulations
            given a batch of parameters and optional context variables.
        simulator_fun       : callable
            A function (callable object) with optional control arguments responsible for generating a simulaiton given
            a single parameter vector and optional variables.
        context generator   : callable (default None, recommended instance of ContextGenerator)
            An optional function (ideally an instance of ContextGenerator) for generating prior context variables.
        """

        if (batch_simulator_fun is None) is (simulator_fun is None):
            raise ConfigurationError('Either batch_simulator_fun or simulator_fun should be provided, but not both!')
        
        self.is_batched = True if batch_simulator_fun is not None else False
        
        if self.is_batched:
            self.simulator = batch_simulator_fun
        else:
            self.simulator = simulator_fun
        self.context_gen = context_generator
        
    def __call__(self, params, *args, **kwargs):
        """ Generates simulated data given param draws and optional context variables generated internally.
        
        Parameters
        ----------
        params   :  np.ndarray of shape (n_sim, ...) - the parameter draws obtained from the prior.

        Returns
        -------

        out_dict : dictionary
            An output dictionary with randomly simulated variables, the following keys are mandatory, if default keys not modified:
            `sim_data` : value
            `non_batchable_context` : value
            `batchable_context` : value
        """
        
        # Always assume first dimension is batch dimension
        batch_size = params.shape[0]
        
        # Prepare placeholder dictionary
        out_dict = {
            DEFAULT_KEYS['sim_data']: None,
            DEFAULT_KEYS['batchable_context'] : None,
            DEFAULT_KEYS['non_batchable_context'] : None
        }
        
        # Populate dictionary with context or leave at None
        if self.context_gen is not None:
            context_dict = self.context_gen.generate_context(batch_size, *args, **kwargs)
            out_dict[DEFAULT_KEYS['non_batchable_context']] = context_dict[DEFAULT_KEYS['non_batchable_context']]
            out_dict[DEFAULT_KEYS['batchable_context']] = context_dict[DEFAULT_KEYS['batchable_context']]
        
        if self.is_batched:
            return self._simulate_batched(params, out_dict, *args, **kwargs)
        return self._simulate_non_batched(params, out_dict, *args, **kwargs)
        
    def _simulate_batched(self, params, out_dict, *args, **kwargs):
        """ Assumes a batched simulator accepting batched contexts and priors.
        """
        
        # No context type
        if out_dict[DEFAULT_KEYS['batchable_context']] is None and out_dict[DEFAULT_KEYS['non_batchable_context']] is None:
            out_dict[DEFAULT_KEYS['sim_data']] = self.simulator(params, *args, **kwargs)
            
        # Only batchable context
        elif out_dict['non_batchable_context'] is None:
            out_dict[DEFAULT_KEYS['sim_data']] = self.simulator(params, 
                                                  out_dict[DEFAULT_KEYS['batchable_context']], *args, **kwargs)

        # Only non-batchable context
        elif out_dict[DEFAULT_KEYS['batchable_context']] is None:
            out_dict[DEFAULT_KEYS['sim_data']] = self.simulator(params, 
                                                  out_dict[DEFAULT_KEYS['non_batchable_context']], *args, **kwargs)
        
        # Both batchable and non-batchable context
        else:
            out_dict[DEFAULT_KEYS['sim_data']] = self.simulator(params, 
                                                  out_dict[DEFAULT_KEYS['batchable_context']], 
                                                  out_dict[DEFAULT_KEYS['non_batchable_context']], *args, **kwargs)

        return out_dict
    
    def _simulate_non_batched(self, params, out_dict, *args, **kwargs):
        """ Assumes a non-batched simulator accepting batched contexts and priors.
        """
        
        # Extract batch size
        batch_size = params.shape[0]
        
        # No context type
        if out_dict[DEFAULT_KEYS['batchable_context']] is None and out_dict[DEFAULT_KEYS['non_batchable_context']] is None:
            out_dict[DEFAULT_KEYS['sim_data']] = np.array([self.simulator(params[b],  *args, **kwargs) for b in range(batch_size)])
            
        # Only batchable context
        elif out_dict['non_batchable_context'] is None:
            out_dict[DEFAULT_KEYS['sim_data']] = np.array([self.simulator(params[b], 
                                                            out_dict[DEFAULT_KEYS['batchable_context']][b], 
                                                            *args, **kwargs) 
                                             for b in range(batch_size)])
            
        # Only non-batchable context
        elif out_dict[DEFAULT_KEYS['batchable_context']] is None:
            out_dict[DEFAULT_KEYS['sim_data']] = np.array([self.simulator(params[b], 
                                                            out_dict[DEFAULT_KEYS['non_batchable_context']], 
                                                            *args, **kwargs) 
                                             for b in range(batch_size)])
            
        # Both batchable and non_batchable context
        else:
            out_dict[DEFAULT_KEYS['sim_data']] = np.array([self.simulator(params[b], 
                                                            out_dict[DEFAULT_KEYS['batchable_context']][b], 
                                                            out_dict[DEFAULT_KEYS['non_batchable_context']], 
                                                            *args, **kwargs) 
                                             for b in range(batch_size)])

        return out_dict
                

class GenerativeModel:
    """Basic interface for a generative model in a simulation-based context.
    Generally, a generative model consists of two mandatory components:
    
    - Prior : A randomized function returning random parameter draws from a prior distribution;
    - Simulator : A function which transforms the parameters into observables in a non-deterministic manner.
    """

    _N_SIM_TEST = 2 
    
    def __init__(self, prior: callable, simulator: callable, skip_test: bool = False, 
                 prior_is_batched: bool = False, simulator_is_batched: bool = False, name: str = "anonymous"):
        """Instantiates a generative model responsible for drawing generating params, data, and optional context.
        
        Parameters
        ----------
        prior                : callable or bayesflow.forward_inference.Prior instance
            A function returning random draws from the prior parameter distribution. Should encode
            prior knowledge about plausible parameter ranges;
        simulator            : callable or bayesflow.forward_inference.Simulator instance
            A function accepting parameter draws, optional context, and optional arguments as input
            and returning obseravble data;
        skip_test            : bool (default - False)
            If True, a forward inference pass will be performed.
        prior_is_batched     : bool (default - False), only relevant and mandatory if providing a custom prior without
            the Prior wrapper.  
        simulator_is_batched : bool (default - False), only relevant and mandatory if providing a custom simulator without
            the Simulator wrapper. 
        name                 : str (default - "anonoymous")
            An optional name for the generative model. If kept default (None), 'anonymous' is set as name.

        Important
        ----------
        If you are not using the provided Prior and Simulator wrappers for your prior and data generator,
        only functions returning a np.ndarray in the correct format will be accepted, since these will be
        wrapped internally. In addition, you need to indicate whether your simulator operates on batched of
        parameters or on single parameter vectors via tha `simulator_is_batched` argument.
        """
        
        if type(prior) is not Prior:
            prior_args = {'batch_prior_fun': prior} if prior_is_batched else {'prior_fun': prior}
            self.prior = Prior(**prior_args)
            self.prior_is_batched = prior_is_batched
        else:
            self.prior = prior
            self.prior_is_batched = prior_is_batched

        if type(simulator) is not Simulator:
            self.simulator = self._config_custom_simulator(simulator, simulator_is_batched)
        else:
            self.simulator = simulator
            self.simulator_is_batched = self.simulator.is_batched

        if name is None:
            self.name = 'anonymous'
        else:
            self.name = name

        self.param_names = self.prior.param_names
        
        if not skip_test:
            self._test()

    def __call__(self, batch_size, *args, **kwargs):
        """ Carries out forward inference 'batch_size' times.
        """

        # Forward inference
        prior_out = self.prior(batch_size, *args, **kwargs)
        sim_out = self.simulator(prior_out['prior_draws'], *args, **kwargs)

        # Prepare and fill placeholder dict
        out_dict = {
            DEFAULT_KEYS['prior_non_batchable_context']: prior_out[DEFAULT_KEYS['non_batchable_context']],
            DEFAULT_KEYS['prior_batchable_context']: prior_out[DEFAULT_KEYS['batchable_context']],
            DEFAULT_KEYS['prior_draws']:  prior_out[DEFAULT_KEYS['prior_draws']],
            DEFAULT_KEYS['sim_non_batchable_context']: sim_out[DEFAULT_KEYS['non_batchable_context']],
            DEFAULT_KEYS['sim_batchable_context']: sim_out[DEFAULT_KEYS['batchable_context']],
            DEFAULT_KEYS['sim_data']: sim_out[DEFAULT_KEYS['sim_data']]
        }

        return out_dict

    def _config_custom_simulator(self, sim_fun, is_batched):
        """ Only called if user has provided a custom simulator not using the Simulator wrapper.
        """

        if is_batched is None:
            raise ConfigurationError('Since you are not using the Simulator wrapper, please set ' +
                                     'simulator_is_batched to True if your simulator operates on batches, ' +
                                     'otherwise set it to False.')
        elif is_batched:
            return Simulator(batch_simulator_fun=sim_fun)
        else:
            return Simulator(simulator_fun=sim_fun)

    def plot_pushforward(self, parameter_draws=None, funcs_list=None, funcs_labels=None, batch_size=1000, show_raw_sims=True):
        """ Creates simulations from parameter_draws (generated from self.prior if they are not passed as an argument)
            and plots visualizations for them.

            Parameters
            ----------
            parameter_draws     : np.ndarray of shape (batch_size, num_parameters)
                A sample of parameters. May be drawn from either the prior (which is also the default behavior if no input is specified)
                or from the posterior to do a prior/posterior pushforward.
            funcs_list          : list of callable
                A list of functions that can be used to aggregate simulation data (map a single simulation to a single real value). 
                The default behavior without user input is to use numpy's mean and standard deviation functions.
            funcs_labels        : list of str
                A list of labels for the functions in funcs_list.
                The default behavior without user input is to call the functions "Aggregator function 1, Aggregator function 2, etc."
            batch_size          : int
                The number of prior draws to generate (and then create and visualizes simulations from)
            show_raw_sims       : bool
                Flag determining whether or not a plot of 49 raw (i.e. unaggregated) simulations is generated. 
                Useful for very general data exploration. 

            Returns
            -------
            parameters_draws    : np.ndarray
                The parameters provided by the user or generated internally.
            simulations         : np.ndarray
                The simulations generated from parameter_draws (or prior draws generated on the fly)
            aggregated_data     : list of np.ndarray
                Arrays generated from the simulations with the functions in funcs_list
            """
       
        if parameter_draws is None:
            parameter_draws = self.prior(batch_size=batch_size)['prior_draws']
        
        simulations = self.simulator(params=parameter_draws)['sim_data']

        if funcs_list is not None and funcs_labels is None:
            funcs_labels = [f"Aggregator function {i+1}" for i in range(len(funcs_list))]

        if funcs_list is None:
            funcs_list = [np.mean, np.std]
            funcs_labels = ["Simulation mean", "Simulation standard deviation"]

        if show_raw_sims:
            if len(simulations.shape) != 2:
                logging.warn("Cannot plot raw simulations since they are not one-dimensional.") 
            else:
                k = min(int(np.ceil(np.sqrt(batch_size))),7)
                f, axarr = plt.subplots(k, k, figsize=(20, 10))
                for i, ax in enumerate(axarr.flat):
                    if i == batch_size:
                        break
                    x = simulations[i]
                    ax.plot(x)
                f.suptitle(f"Raw Data for {k*k} Simulations", fontsize=16)
                f.tight_layout()
            
        funcs_count = len(funcs_list)
        g, axarr = plt.subplots(funcs_count, 1, figsize=(20,10))
        aggregated_data = []

        for i, ax in enumerate(axarr.flat):
            x = [funcs_list[i](simulations[l]) for l in range(batch_size)]
            aggregated_data += [x]
            ax.set_title(funcs_labels[i])
            ax.set_xlabel("Simulation")
            ax.set_ylabel("Aggregated value")
            ax.plot(x)
        g.suptitle("Aggregated Measures of Simulations", fontsize=16)
        g.tight_layout()

        output_dict = {
            'parameter_draws': parameter_draws, 
            'simulations': simulations, 
            'aggregated_data': aggregated_data, 
            'functions_used': funcs_list, 
            'function_names': funcs_labels
        }
        return output_dict

    def _test(self):
        """Performs a sanity check on forward inference and some verbose information."""

        # Use minimal n_sim > 1
        _n_sim = GenerativeModel._N_SIM_TEST
        out = self(_n_sim)

        # Logger
        logger = logging.getLogger()
        logger.setLevel(logging.INFO)

        # Attempt to log batch results or fail and warn user
        try:
            logger.info(f'Performing {_n_sim} pilot runs with the {self.name} model...')
            # Format strings
            p_shape_str = "(batch_size = {}, -{}".format(out[DEFAULT_KEYS['prior_draws']].shape[0], 
                                                         out[DEFAULT_KEYS['prior_draws']].shape[1:])
            p_shape_str = p_shape_str.replace('-(', '').replace(',)', ')')
            d_shape_str = "(batch_size = {}, -{}".format(out[DEFAULT_KEYS['sim_data']].shape[0], 
                                                         out[DEFAULT_KEYS['sim_data']].shape[1:])
            d_shape_str = d_shape_str.replace('-(', '').replace(',)', ')')

            # Log to default-config
            logger.info(f'Shape of parameter batch after {_n_sim} pilot simulations: {p_shape_str}')
            logger.info(f'Shape of simulation batch after {_n_sim} pilot simulations: {d_shape_str}')

            for k, v in out.items():
                if 'context' in k:
                    name = k.replace('_', ' ').replace('sim', 'simulation').replace('non ', 'non-')
                    if v is None:
                        logger.info(f'No optional {name} provided.')
                    else:
                        try:
                            logger.info(f'Shape of {name}: {v.shape}')
                        except Exception as _:
                            logger.info(f'Could not determine shape of {name}. Type appears to be non-array: {type(v)},\
                                    so make sure your input configurator takes cares of that!')
        except Exception as err:
            raise ConfigurationError('Could not run forward inference with specified generative model...' +
                                    f'Please re-examine model components!\n {err}')
        
    def presimulate_and_save(self, batch_size, folder_path, total_iterations=None, memory_limit=None, 
                             iterations_per_epoch = None, epochs = None, extend_from=0):

        """ Simulates a dataset for single-pass offline training (called via the train_from_presimulation method 
        of the Trainer class in the trainers.py script).

        Parameters
        ----------
        batch_size           : int
            Number of simulations which will be used in each backpropagation step of training.
        total_iterations     : int
            Total number of iterations to perform during training. If total_iterations divided by epochs is not an integer, it
            will be increased so that said division does result in an integer.
        memory_limit         : int
            Upper bound on the size of individual files (in Mb); can be useful to avoid running out of RAM during training.
        iterations_per_epoch : int
            Number of batch simulations to perform per epoch file. If iterations_per_epoch batches per file lead to files
            exceeding the memory_limit, iterations_per_epoch will be lowered so that the memory_limit can be enforced.
        epochs               : int
            Number of epoch files to generate. A higher number will be generated if the memory_limit for individual files requires it.
        extend_from          : int
            If folder_path already contains presimulations and the user wishes to add further simulations to these, 
            extend_from must provide the number of the last presimulation file in folder_path.
        
        Important
        ----------
        One of the following pairs of parameters has to be provided: 
        
        - (iterations_per_epoch, epochs), 
        - (total_iterations, iterations_per_epoch)
        - (total_iterations, epochs)

        Providing all three of the parameters in these pairs leads to a consistency check, since incompatible combinations are possible. 
      """
        
        # Ensure that the combination of parameters provided is sufficient to perform presimulation and does not contain internal contradictions
        if total_iterations is not None and iterations_per_epoch is not None and epochs is not None:
            if iterations_per_epoch*epochs != total_iterations:
                raise ValueError ('The product of the number of epochs and the number of iterations per epoch provided is not equal to the total number of iterations.')
        else:
            none_ctr = 0
            for parameter in [total_iterations, iterations_per_epoch, epochs]:
                if parameter is None:
                    none_ctr += 1
            if none_ctr > 1:
                raise ValueError ('Missing required parameters. At least two of the following must be provided: total_iterations, iterations_per_epoch and epochs.')

        # Compute missing epochs parameter if necessary
        if epochs is None:
            epochs = total_iterations/iterations_per_epoch
            if int(epochs) < epochs:
                epochs = int(epochs)+1
                logging.info(f"Setting number of epochs to {epochs} and upping total number of iterations to {epochs*iterations_per_epoch} in order to create files of the same size.")
                total_iterations = epochs*iterations_per_epoch
            else:
                epochs = int(epochs)

        # Determine the disk space required to save a file containing a single batch
        test_batch = self.__call__(batch_size=batch_size)
        with open('test_batch.pkl', 'wb') as f:
            pickle.dump(test_batch, f)
        batch_space = (10**(-6)) * os.path.getsize('test_batch.pkl')
        os.remove('test_batch.pkl')

        # Compute parameters not given
        if total_iterations is None:
            total_iterations = iterations_per_epoch*epochs
        elif iterations_per_epoch is None:
            iterations_per_epoch = total_iterations/epochs
            if int(iterations_per_epoch) < total_iterations/epochs:
                iterations_per_epoch = int(iterations_per_epoch)+1
                total_iterations = iterations_per_epoch*epochs
                logging.info(f"Setting number of iterations per epoch to {iterations_per_epoch} and upping total number of iterations to {total_iterations} \
                    to create files of the same size and ensure that no less than the specified total number of iterations is simulated.")

        # Ensure the folder path is interpreted as a directory and not a file
        if folder_path[-1] != '/':
            folder_path += '/'

        # Compute  the total space requirement and get a prompt from users confirming the start of the presimulation process
        required_space = total_iterations*batch_space
        if extend_from > 0:
            logging.info("You have chosen to extend an existing dataset.")
            extension = 'extension'
        else:
            extension = ''
        logging.warn(f"The presimulated dataset {extension} will take up {required_space} Mb of disk space.")
        user_choice = input("Are you sure you want to perform presimulation? (y/n)")
        
        if user_choice.find('y') != -1 or user_choice.find('Y') != -1:
            logging.info("Performing presimulation...")
        else:
            logging.info("Presimulation aborted.")
            return None
        
        if extend_from > 0:
            if not os.path.isdir(folder_path):
                logging.warn(f"Cannot extend dataset in {folder_path} - folder does not exist. Creating folder and saving presimulated dataset extension inside.")
            else:
                already_simulated = len(os.listdir(folder_path))
                if already_simulated != extend_from:
                    logging.warn(f"The parameter you provided for extend_from does not match the actual number of files found in {folder_path}. File numbering may now prove erroneous.")
            
        # Choose a number of batches per file as specified via iterations_per_epoch unless
        # the memory_limit per file forces a smaller choice, in which case the highest permissible
        # value is chosen.
        if memory_limit is None:
            batches_per_file = iterations_per_epoch
        else:
            batches_per_file = min(int(memory_limit/batch_space), iterations_per_epoch)
            if batches_per_file < iterations_per_epoch:
                logging.warn(f"Number of iterations per epoch was reduced to {batches_per_file} to ensure that the memory limit per file is not exceeded.")
        
        file_space = batches_per_file*batch_space
        
        # If folder_path does not exist yet, create it
        if not os.path.isdir(folder_path):
            os.mkdir(folder_path)
        
        # Compute as many priors as would have been computed when generating the original dataset.
        # If a fixed random seed was used, this will move it forward, and computational cost is neglible (under 1/200000 of simulation time)
        if extend_from > 0:
            previous_priors = self.prior(batch_size = batch_size*iterations_per_epoch*extend_from)

        # Ensure that the total number of iterations given or inferred is met (or exceeeded) whilst not violating the memory limit
        total_files = total_iterations/batches_per_file
        if int(total_files) < total_files:
            total_files = int(total_files)+1
            if total_files > epochs:
                logging.info(f"Increased number of files (i.e. epochs) to {total_files} to ensure that the memory limit is not exceeded but the total number of iterations is met.")
        else:
            total_files = int(total_files)
        logging.info(f"Generating {total_files} files of size {file_space} Mb containing {batches_per_file} batches each.")

        # Generate the presimulation files    
        file_counter = extend_from
        for i in range(total_files):
            with tqdm(total=batches_per_file, desc=f'Batches generated for file {i+1}') as p_bar:
                file_list = [{} for _ in range(batches_per_file)]
                for k in range(batches_per_file):
                    file_list[k] = self.__call__(batch_size=batch_size)
                    p_bar.update(1)
                with open(folder_path+'presim_file_'+str(file_counter+1)+'.pkl', 'wb+') as f:
                    pickle.dump(file_list, f)
                file_counter +=1
                
        logging.info(f"Presimulation {extension} complete. Generated {total_files} files.")    

class MultiGenerativeModel:
    """Basic interface for multiple generative models in a simulation-based context.
    A ``MultiveGenerativeModel`` instance consists of a list of ``GenerativeModel`` instances
    and a prior distribution over candidate models defined by a list of probabilities.
    """

    def __init__(self, generative_models: list, model_probs='equal'):
        """Instantiates a multi-generative model responsible for generating parameters, data, and optional context
        from a list of models according to specified prior model probabilities (PMPs).
        
        Parameters
        ----------
        generative_models : list of GenerativeModel instances
            The list of candidate generative models
        model_probs       : string (default - 'equal') or list of floats with sum(model_probs) == 1.
            The list of model probabilities, should have the same length as the list of
            generative models. Note, that probabilities should sum to one.
        """

        self.generative_models = generative_models
        self.num_models = len(generative_models)
        self.model_prior = self._determine_model_prior(model_probs)

    def _determine_model_prior(self, model_probs):
        """Creates the model prior p(M) given user input."""

        if model_probs == 'equal':
            return lambda b: np.random.default_rng().integers(low=0, high=self.num_models, size=b)
        return lambda b: np.random.default_rng().choice(self.num_models, size=b, p=model_probs)
        
    def __call__(self, batch_size, **kwargs):
        
        # Prepare placeholders
        out_dict = {
            DEFAULT_KEYS['model_outputs']: [],
            DEFAULT_KEYS['model_indices']: []
        }
        
        # Sample model indices
        model_samples = self.model_prior(batch_size)

        # gather model indices and simulate datasets of same model index as batch
        # create frequency table of model indices
        model_indices, counts = np.unique(model_samples, return_counts=True)

        # Iterate over each unique model index and create all data sets for that model index
        for m, batch_size_m in zip(model_indices, counts):
            model_out = self.generative_models[m](batch_size_m, **kwargs)
            out_dict[DEFAULT_KEYS['model_outputs']].append(model_out)
            out_dict[DEFAULT_KEYS['model_indices']].append(m)
        return out_dict
