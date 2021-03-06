from GenModels.GM.Distributions import Categorical, Dirichlet, TensorTransitionDirichletPrior
from .DiscreteGraphParameters import *
import autograd.numpy as np
from autograd import grad, value_and_grad, jacobian
from autograd.misc.optimizers import adam, unrolledGrad
from autograd.extend import primitive, defvjp
from autograd.misc import flatten
import copy
from functools import partial
import string
from GenModels.GM.Utility import logsumexp, extendAxes, logMultiplyTerms, logIntegrate

__all__ = [ 'Gibbs',
            'GroupGibbs',
            'EM',
            'GroupEM',
            'CAVI',
            'GroupCAVI',
            'SVI',
            'GroupSVI',
            'SVAE',
            'GroupSVAE',
            'DESOpt',
            'GroupDESOpt' ]

######################################################################

class Optimizer():

    def __init__( self, msg, parameters ):
        self.msg = msg
        self.params = parameters
        self.U = None
        self.V = None

    def loadData( self, graphs ):
        self.msg.preprocessData( graphs )

    def runFilter( self ):
        self.U, self.V = self.msg.filter()

    def fitStep( self ):
        raise NotImplementedError

######################################################################

class GraphSmoothedState():

    def __init__( self, msg, U, V ):
        self.msg = msg
        self.node_states = {}
        self.U = U
        self.V = V

    def __call__( self, node_list ):
        # Compute P( x_c | x_p1..pN, Y )
        vals = self.msg.conditionalParentChild( self.U, self.V, node_list )

        for node, probs in vals:
            parents, parent_order = self.msg.getParents( node, get_order=True )
            if( len( parents ) == 0 ):
                prob = probs
            else:
                indices = tuple( [ [ self.node_states[ p ] ] for p in parents ] )
                prob = probs[ indices ].ravel()

            # Sample from P( x_c | x_p1..pN, Y )
            state = Categorical.sample( nat_params=( prob, ) )[ 0 ]
            self.node_states[ node ] = state

######################################################################

class Gibbs( Optimizer ):

    def __init__( self, msg, parameters ):
        super().__init__( msg, parameters )
        self.graph_state = None

    def resampleStates( self, return_marginal=False ):
        self.msg.updateParams( self.params.sampleInitialDist(), self.params.sampleTransitionDist(), self.params.sampleEmissionDist() )
        self.runFilter()
        self.graph_state = GraphSmoothedState( self.msg, self.U, self.V )
        self.msg.forwardPass( self.graph_state )
        if( return_marginal ):
            return self.msg.marginalProb( self.U, self.V )
        return None

    def resampleParameters( self ):
        self.params.resampleInitialDist( self.msg, self.graph_state )
        self.params.resampleTransitionDist( self.msg, self.graph_state )
        self.params.resampleEmissionDist( self.msg, self.graph_state )

    def stateUpdate( self, return_marginal=False ):
        return self.resampleStates( return_marginal=return_marginal )

    def fitStep( self, return_marginal=False ):
        ret_val = self.resampleStates( return_marginal=return_marginal )
        self.resampleParameters()
        return ret_val

    def genProbHelper( self, node_list ):
        # Compute P( X, Y | Θ )
        for node in node_list:
            node_state = self.graph_state.node_states[ node ]

            # P( X | Θ )
            if( self.msg.nParents( node ) == 0 ):
                self.gen_prob += self.params.initial_dist.ilog_likelihood( np.array( [ node_state ] ) )
            else:
                parents, parent_order = self.msg.getParents( node, get_order=True )
                states = tuple( [ np.array( [ self.graph_state.node_states[ p ] ] ) for p in parents ] )
                transition_state = states + ( np.array( [ node_state ] ), )
                self.gen_prob += self.params.transition_dist.ilog_likelihood( transition_state )

            # P( Y | X, Θ )
            emission_state = ( np.array( [ node_state ] ), np.array( [ self.msg.ys[ node ] ] ) )
            self.gen_prob += self.params.emission_dist.ilog_likelihood( emission_state )

    def generativeProbability( self ):
        self.gen_prob = 0.0
        self.msg.forwardPass( self.genProbHelper )
        return self.gen_prob

######################################################################

class GroupGibbs( Gibbs ):

    def __init__( self, msg, parameters ):
        super().__init__( msg, parameters )

    def genProbHelper( self, node_list ):
        # Compute P( X, Y | Θ )
        for node in node_list:
            node_state = self.graph_state.node_states[ node ]
            group = self.msg.node_groups[ node ]

            # P( X | Θ )
            if( self.msg.nParents( node ) == 0 ):
                self.gen_prob += self.params.initial_dists[ group ].ilog_likelihood( np.array( [ node_state ] ) )
            else:
                parents, parent_order = self.msg.getParents( node, get_order=True )
                states = tuple( [ np.array( [ self.graph_state.node_states[ p ] ] ) for p in parents ] )
                transition_state = states + ( np.array( [ node_state ] ), )
                self.gen_prob += self.params.transition_dists[ group ].ilog_likelihood( transition_state )

            # P( Y | X, Θ )
            emission_state = ( np.array( [ node_state ] ), np.array( [ self.msg.ys[ node ] ] ) )
            self.gen_prob += self.params.emission_dists[ group ].ilog_likelihood( emission_state )

######################################################################

class EM( Optimizer ):

    def __init__( self, msg, parameters ):
        super().__init__( msg, parameters )

    def EStep( self ):
        self.msg.updateParams( self.params.initial_dist.pi, [ dist.pi for dist in self.params.transition_dists ], self.params.emission_dist.pi )
        self.runFilter()

        marginal = self.msg.marginalProb( self.U, self.V )

        # Compute log P( x | Y ), log P( x_p1..pN | Y ) and log P( x_c, x_p1..pN | Y )
        node_parents_smoothed = self.msg.parentChildSmoothed( self.U, self.V, self.msg.nodes )
        parents_smoothed = self.msg.parentsSmoothed( self.U, self.V, self.msg.nodes, node_parents_smoothed )
        node_smoothed = self.msg.nodeSmoothed( self.U, self.V, self.msg.nodes, node_parents_smoothed )

        # The probabilities are normalized, so don't need them in log space anymore
        node_smoothed = [ ( n, np.exp( val ) ) for n, val in node_smoothed ]
        parents_smoothed = [ ( n, np.exp( val ) ) for n, val in parents_smoothed ]
        node_parents_smoothed = [ ( n, np.exp( val ) ) for n, val in node_parents_smoothed ]

        return dict( node_smoothed ), dict( parents_smoothed ), dict( node_parents_smoothed ), marginal

    def MStep( self, node_smoothed, parents_smoothed, node_parents_smoothed ):

        self.params.updateInitialDist( self.msg, node_smoothed )
        self.params.updateTransitionDist( self.msg, parents_smoothed, node_parents_smoothed )
        self.params.updateEmissionDist( self.msg, node_smoothed )

    def stateUpdate( self ):
        node_smoothed, parents_smoothed, node_parents_smoothed, marginal = self.EStep()
        return node_smoothed, marginal

    def fitStep( self ):
        node_smoothed, parents_smoothed, node_parents_smoothed, marginal = self.EStep()
        self.MStep( node_smoothed, parents_smoothed, node_parents_smoothed )
        return marginal

######################################################################

class GroupEM( EM ):

    def __init__( self, msg, parameters ):
        super().__init__( msg, parameters )

    def EStep( self ):
        pi0s = dict( [ ( group, dist.pi ) for group, dist in self.params.initial_dists.items() ] )
        pis = dict( [ ( group, [ dist.pi for shape, dist in dists.items() ] ) for group, dists in self.params.transition_dists.items() ] )
        Ls = dict( [ ( group, dist.pi ) for group, dist in self.params.emission_dists.items() ] )
        self.msg.updateParams( pi0s, pis, Ls )
        self.runFilter()

        marginal = self.msg.marginalProb( self.U, self.V )

        # Compute log P( x | Y ), log P( x_p1..pN | Y ) and log P( x_c, x_p1..pN | Y )
        node_smoothed = self.msg.nodeSmoothed( self.U, self.V, self.msg.nodes )
        parents_smoothed = self.msg.parentsSmoothed( self.U, self.V, self.msg.nodes )
        node_parents_smoothed = self.msg.parentChildSmoothed( self.U, self.V, self.msg.nodes )

        # The probabilities are normalized, so don't need them in log space anymore
        node_smoothed = [ ( n, np.exp( val ) ) for n, val in node_smoothed ]
        parents_smoothed = [ ( n, np.exp( val ) ) for n, val in parents_smoothed ]
        node_parents_smoothed = [ ( n, np.exp( val ) ) for n, val in node_parents_smoothed ]

        return dict( node_smoothed ), dict( parents_smoothed ), dict( node_parents_smoothed ), marginal

######################################################################

class CAVI( Optimizer ):
    # Coordinate ascent variational inference

    def __init__( self, msg, parameters, from_super=False ):
        super().__init__( msg, parameters )

        if( from_super == False ):
            # Initialize the expected mf nat params using the prior
            self.initial_prior_mfnp     = self.params.initial_dist.prior.nat_params
            self.transition_prior_mfnps = [ dist.prior.nat_params for dist in self.params.transition_dists ]
            self.emission_prior_mfnp    = self.params.emission_dist.prior.nat_params

    def ELBO( self, initial_prior_mfnp, transition_prior_mfnps, emission_prior_mfnp ):
        normalizer = self.msg.marginalProb( self.U, self.V )

        initial_kl_divergence = self.params.initial_dist.KLDivergence( nat_params1=initial_prior_mfnp, nat_params2=self.params.initial_dist.prior.nat_params )
        transition_kl_divergence = 0
        for mfnp, dist in zip( transition_prior_mfnps, self.params.transition_dists ):
            transition_kl_divergence += dist.KLDivergence( nat_params1=mfnp, nat_params2=dist.prior.nat_params )
        emission_kl_divergence = self.params.emission_dist.KLDivergence( nat_params1=emission_prior_mfnp, nat_params2=self.params.emission_dist.prior.nat_params )

        return normalizer - ( initial_kl_divergence + transition_kl_divergence + emission_kl_divergence )

    def variationalEStep( self, initial_prior_mfnp, transition_prior_mfnps, emission_prior_mfnp ):

        # Filter using the expected natural parameters
        expected_initial_nat_params    = self.params.initial_dist.expectedNatParams( prior_nat_params=initial_prior_mfnp )[ 0 ]
        expected_transition_nat_params = [ dist.expectedNatParams( prior_nat_params=mfnp )[ 0 ] for dist, mfnp in zip( self.params.transition_dists, transition_prior_mfnps ) ]
        expected_emission_nat_params   = self.params.emission_dist.expectedNatParams( prior_nat_params=emission_prior_mfnp )[ 0 ]

        self.msg.updateNatParams( expected_initial_nat_params, expected_transition_nat_params, expected_emission_nat_params, check_parameters=False )
        self.runFilter()

        elbo = self.ELBO( initial_prior_mfnp, transition_prior_mfnps, emission_prior_mfnp )

        # Compute log P( x | Y ) and log P( x_c, x_p1..pN | Y )
        node_parents_smoothed = self.msg.parentChildSmoothed( self.U, self.V, self.msg.nodes )
        node_smoothed = self.msg.nodeSmoothed( self.U, self.V, self.msg.nodes, node_parents_smoothed )

        # The probabilities are normalized, so don't need them in log space anymore
        node_smoothed = [ ( n, np.exp( val ) ) for n, val in node_smoothed ]
        node_parents_smoothed = [ ( n, np.exp( val ) ) for n, val in node_parents_smoothed ]

        return dict( node_smoothed ), dict( node_parents_smoothed ), elbo

    def variationalMStep( self, node_smoothed, node_parents_smoothed ):
        initial_prior_mfnp     = self.params.updatedInitialPrior( self.msg, node_smoothed )
        transition_prior_mfnps = self.params.updatedTransitionPrior( self.msg, node_parents_smoothed )
        emission_prior_mfnp    = self.params.updatedEmissionPrior( self.msg, node_smoothed )

        return initial_prior_mfnp, transition_prior_mfnps, emission_prior_mfnp

    def stateUpdate( self ):
        node_smoothed, node_parents_smoothed, elbo = self.variationalEStep( self.initial_prior_mfnp, self.transition_prior_mfnps, self.emission_prior_mfnp )
        return elbo

    def fitStep( self ):
        node_smoothed, node_parents_smoothed, elbo = self.variationalEStep( self.initial_prior_mfnp, self.transition_prior_mfnps, self.emission_prior_mfnp )
        self.initial_prior_mfnp, self.transition_prior_mfnps, self.emission_prior_mfnp = self.variationalMStep( node_smoothed, node_parents_smoothed )
        return elbo

######################################################################

class GroupCAVI( CAVI ):
    # Coordinate ascent variational inference

    def __init__( self, msg, parameters ):
        super().__init__( msg, parameters, from_super=True )

        # Initialize the expected mf nat params using the prior
        self.initial_prior_mfnp    = dict( [ ( group, dist.prior.nat_params ) for group, dist in self.params.initial_dists.items() ] )
        self.transition_prior_mfnps = dict( [ ( group, dict( [ ( shape, dist.prior.nat_params ) for shape, dist in dists.items() ] ) ) for group, dists in self.params.transition_dists.items() ] )
        self.emission_prior_mfnp   = dict( [ ( group, dist.prior.nat_params ) for group, dist in self.params.emission_dists.items() ] )

    def ELBO( self, initial_prior_mfnp, transition_prior_mfnps, emission_prior_mfnp ):
        normalizer = self.msg.marginalProb( self.U, self.V )

        initial_kl_divergence, transition_kl_divergence, emission_kl_divergence = 0, 0, 0

        for group in self.params.initial_dists.keys():
            initial_kl_divergence += self.params.initial_dists[ group ].KLDivergence( nat_params1=initial_prior_mfnp[ group ], nat_params2=self.params.initial_dists[ group ].prior.nat_params )
            transition_kl_divergence = 0
            for shape in transition_prior_mfnps[ group ].keys():
                mfnp = transition_prior_mfnps[ group ][ shape ]
                dist = self.params.transition_dists[ group ][ shape ]
                transition_kl_divergence += dist.KLDivergence( nat_params1=mfnp, nat_params2=dist.prior.nat_params )
            emission_kl_divergence   += self.params.emission_dists[ group ].KLDivergence( nat_params1=emission_prior_mfnp[ group ], nat_params2=self.params.emission_dists[ group ].prior.nat_params )

        return normalizer - ( initial_kl_divergence + transition_kl_divergence + emission_kl_divergence )

    def variationalEStep( self, initial_prior_mfnp, transition_prior_mfnps, emission_prior_mfnp ):

        # Filter using the expected natural parameters
        expected_initial_nat_params    = dict( [ ( group, dist.prior.expectedSufficientStats( nat_params=initial_prior_mfnp[ group ] )[ 0 ] ) for group, dist in self.params.initial_dists.items() ] )
        expected_transition_nat_params = dict( [ ( group, [ dist.prior.expectedSufficientStats( nat_params=transition_prior_mfnps[ group ][ shape ] )[ 0 ] for shape, dist in dists.items() ] ) for group, dists in self.params.transition_dists.items() ] )
        expected_emission_nat_params   = dict( [ ( group, dist.prior.expectedSufficientStats( nat_params=emission_prior_mfnp[ group ] )[ 0 ] ) for group, dist in self.params.emission_dists.items() ] )

        self.msg.updateNatParams( expected_initial_nat_params, expected_transition_nat_params, expected_emission_nat_params, check_parameters=False )
        self.runFilter()

        elbo = self.ELBO( initial_prior_mfnp, transition_prior_mfnps, emission_prior_mfnp )

        # Compute log P( x | Y ) and log P( x_c, x_p1..pN | Y )
        node_smoothed = self.msg.nodeSmoothed( self.U, self.V, self.msg.nodes )
        node_parents_smoothed = self.msg.parentChildSmoothed( self.U, self.V, self.msg.nodes )

        # The probabilities are normalized, so don't need them in log space anymore
        node_smoothed = [ ( n, np.exp( val ) ) for n, val in node_smoothed ]
        node_parents_smoothed = [ ( n, np.exp( val ) ) for n, val in node_parents_smoothed ]

        return dict( node_smoothed ), dict( node_parents_smoothed ), elbo

######################################################################

class SVI( CAVI ):

    # Stochasic variational inference
    def __init__( self, msg, parameters, minibatch_ratio, step_size ):
        super().__init__( msg, parameters )
        self.s = minibatch_ratio
        self.params.setMinibatchRatio( self.s )
        assert step_size >= 0 and step_size <= 1
        self.p = step_size

    def variationalMStep( self, node_smoothed, node_parents_smoothed ):
        initial_prior_mfnp_update,    = self.params.updatedInitialPrior( self.msg, node_smoothed )
        transition_prior_mfnp_update  = self.params.updatedTransitionPrior( self.msg, node_parents_smoothed )
        emission_prior_mfnp_update,   = self.params.updatedEmissionPrior( self.msg, node_smoothed )

        # Take a natural gradient step
        initial_prior_mfnp     = ( 1 - self.p ) * self.initial_prior_mfnp[ 0 ] + self.p * initial_prior_mfnp_update
        transition_prior_mfnps = [ ( ( 1 - self.p ) * mfnp[ 0 ] + self.p * update[ 0 ], ) for mfnp, update in zip( self.transition_prior_mfnps, transition_prior_mfnp_update ) ]
        emission_prior_mfnp    = ( 1 - self.p ) * self.emission_prior_mfnp[ 0 ] + self.p * emission_prior_mfnp_update
        return ( initial_prior_mfnp, ), transition_prior_mfnps, ( emission_prior_mfnp, )

######################################################################

class GroupSVI( GroupCAVI ):
    # Stochasic variational inference
    def __init__( self, msg, parameters, minibatch_ratio, step_size ):
        super().__init__( msg, parameters )
        self.s = minibatch_ratio
        self.params.setMinibatchRatio( self.s )
        assert step_size >= 0 and step_size <= 1
        self.p = step_size

    def variationalMStep( self, node_smoothed, node_parents_smoothed ):
        initial_prior_mfnp_update    = self.params.updatedInitialPrior( self.msg, node_smoothed )
        transition_prior_mfnp_update = self.params.updatedTransitionPrior( self.msg, node_parents_smoothed )
        emission_prior_mfnp_update   = self.params.updatedEmissionPrior( self.msg, node_smoothed )

        # Take a natural gradient step
        initial_prior_mfnp, transition_prior_mfnps, emission_prior_mfnp = {}, {}, {}
        for group in initial_prior_mfnp_update.keys():

            initial_prior_mfnp[ group ] = ( ( 1 - self.p ) * self.initial_prior_mfnp[ group ][ 0 ] + self.p * initial_prior_mfnp_update[ group ][ 0 ], )

            transition_prior_mfnps[ group ] = {}
            for shape in transition_prior_mfnp_update[ group ].keys():
                update, = transition_prior_mfnp_update[ group ][ shape ]
                mfnp, = self.transition_prior_mfnps[ group ][ shape ]
                transition_prior_mfnps[ group ][ shape ] = ( ( 1 - self.p ) * mfnp + self.p * update, )

            emission_prior_mfnp[ group ] = ( ( 1 - self.p ) * self.emission_prior_mfnp[ group ][ 0 ] + self.p * emission_prior_mfnp_update[ group ][ 0 ], )

        return initial_prior_mfnp, transition_prior_mfnps, emission_prior_mfnp

######################################################################

class RelaxedStateSampler():
    def __init__( self, msg, node_parents_smoothed ):
        self.msg = msg
        self.node_states = {}
        self.conditional_parent_child = dict( self.msg.conditionalParentChild( None, None, None, node_parents_smoothed ) )

    def __call__( self, node_list ):
        # Compute P( x_c | x_p1..pN, Y )

        vals = [ ( node, self.conditional_parent_child[ node ] ) for node in node_list ]

        for node, probs in vals:
            parents, parent_order = self.msg.getParents( node, get_order=True )

            if( len( parents ) == 0 ):
                prob = probs
            else:
                n_parents = len( parents )
                parent_logits = [ extendAxes( self.node_states[ p ], i, n_parents + 1 ) for i, p in enumerate( parents ) ]

                # einsum in log space
                multiplied = logMultiplyTerms( parent_logits + [ probs ] )
                prob = logIntegrate( multiplied, axes=np.arange( n_parents ) )

            # Sample from P( x_c | x_p1..pN, Y )
            relaxed_state = Categorical.reparametrizedSample( nat_params=( prob, ), return_log=True, temp=1.0 )

            self.node_states[ node ] = relaxed_state

######################################################################

class SVAE( Optimizer ):
    # THIS IS AN INCOMPLETE IMPLEMENTATION.  IT WILL ONLY OPTIMIZE THE RECOGNITION AND GENERATIVE NETWORKS.
    # THE FULL IMPLEMENTATION ISN'T NEEDED FOR THE RESEARCH PROJECT
    # Also there was a problem with the full implementation on how to save the node_parents_smoothed for later so that
    # we can compute the natural gradients for the state hyperparameters AND how to get the gradients of the log-likelihood
    # w.r.t. the expected initial nat params and expected transition nat params

    # The emission parameter will hold both the emission network and recognition network
    # The recognition potentials will come from the expected natural parameters (this gets called in the E-step)
    def __init__( self, msg, parameters, minibatch_ratio ):
        super().__init__( msg, parameters )
        self.s = minibatch_ratio
        self.params.setMinibatchRatio( self.s )
        self.initial_prior_mfnp     = copy.deepcopy( self.params.initial_dist.prior.nat_params )
        self.transition_prior_mfnps = [ copy.deepcopy( dist.prior.nat_params ) for dist in self.params.transition_dists ]

    #####################################################################

    def svaeLoss( self, emission_params, n_iter ):

        recognizer_params, generative_hyper_params = emission_params

        # Filter using the expected natural parameters
        expected_initial_nat_params    = self.params.initial_dist.expectedNatParams( prior_nat_params=self.initial_prior_mfnp )[ 0 ]
        expected_transition_nat_params = [ dist.expectedNatParams( prior_nat_params=mfnp )[ 0 ] for dist, mfnp in zip( self.params.transition_dists, self.transition_prior_mfnps ) ]

        # Run the smoother
        recognizer = partial( self.params.emission_dist.recognize, recognizer_params=recognizer_params )
        self.msg.updateNatParams( expected_initial_nat_params, expected_transition_nat_params, recognizer, check_parameters=False )
        self.runFilter()

        # Compute log_q( x_c, x_p1..pN ) so that we can sample from q( X )
        node_parents_smoothed = self.msg.parentChildSmoothed( self.U, self.V, self.msg.nodes )

        # Sample x using the gumbel reparametrization trick
        self.graph_relaxed_state = RelaxedStateSampler( self.msg, node_parents_smoothed )
        self.msg.forwardPass( self.graph_relaxed_state )

        # Compute the kl divergence from p to q.
        # Because we're using q( pi ) := p( pi ) and q( pi0 ) := p( pi0 ), don't need to calculate those terms
        neg_log_z = self.msg.marginalProb( self.U, self.V )
        emission_kl_divergence = self.params.emission_dist.KLPQ( q_params=generative_hyper_params )

        klpq = neg_log_z - emission_kl_divergence

        # Sample a parameter from the generative network
        generative_params = self.params.emission_dist.sampleGenerativeParams( generative_hyper_params=generative_hyper_params )

        # Compute the likelihood of this sample
        log_likelihood = 0.0
        for node, logit in self.graph_relaxed_state.node_states.items():
            log_likelihood += self.params.emission_dist.log_likelihood( x=logit, y=self.msg.ys[ node ], cond=self.msg.conds[ node ], generative_params=generative_params )

        # Compute the total SVAE loss
        svae_loss = self.s * ( log_likelihood - klpq )

        print( 'svae_loss', svae_loss, flush=True )

        return -svae_loss

    #####################################################################

    def trainSVAE( self, num_iters=100 ):

        svae_params = ( self.params.emission_dist.recognizer_params, self.params.emission_dist.generative_hyper_params )

        emission_grads = grad( self.svaeLoss )
        def callback( x, i, g ):
            if( i%25 == 0 ):
                print( 'i', i )

        opt_params = adam( emission_grads, svae_params, num_iters=num_iters, callback=callback )

        # Update the model parameters
        self.params.emission_dist.recognizer_params = opt_params[ 0 ]
        self.params.emission_dist.generative_hyper_params = opt_params[ 1 ]

        return opt_params

######################################################################

class GroupSVAE( Optimizer ):
    # THIS IS AN INCOMPLETE IMPLEMENTATION.  IT WILL ONLY OPTIMIZE THE RECOGNITION AND GENERATIVE NETWORKS.
    # THE FULL IMPLEMENTATION ISN'T NEEDED FOR THE RESEARCH PROJECT
    # Also there was a problem with the full implementation on how to get the gradients of the log-likelihood
    # w.r.t. the expected initial nat params and expected transition nat params

    # The emission parameter will hold both the emission network and recognition network
    # The recognition potentials will come from the expected natural parameters (this gets called in the E-step)
    def __init__( self, msg, parameters, minibatch_ratio ):
        super().__init__( msg, parameters )
        self.s = minibatch_ratio
        self.params.setMinibatchRatio( self.s )
        self.initial_prior_mfnp    = dict( [ ( group, copy.deepcopy( dist.prior.nat_params ) ) for group, dist in self.params.initial_dists.items() ] )
        self.transition_prior_mfnps = dict( [ ( group, dict( [ ( shape, copy.deepcopy( dist.prior.nat_params ) ) for shape, dist in dists.items() ] ) ) for group, dists in self.params.transition_dists.items() ] )

    #####################################################################

    def svaeLoss( self, emission_params, n_iter ):

        recognizer_params, generative_hyper_params = emission_params

        # Filter using the expected natural parameters
        expected_initial_nat_params    = dict( [ ( group, dist.prior.expectedSufficientStats( nat_params=self.initial_prior_mfnp[ group ] )[ 0 ] ) for group, dist in self.params.initial_dists.items() ] )
        expected_transition_nat_params = dict( [ ( group, [ dist.prior.expectedSufficientStats( nat_params=self.transition_prior_mfnps[ group ][ shape ] )[ 0 ] for shape, dist in dists.items() ] ) for group, dists in self.params.transition_dists.items() ] )

        # Run the smoother
        recognizers = {}
        for group, dist in self.params.emission_dists.items():
            recognizers[ group ] = partial( dist.recognize, recognizer_params=recognizer_params[ group ] )

        self.msg.updateNatParams( expected_initial_nat_params, expected_transition_nat_params, recognizers, check_parameters=False )
        self.runFilter()

        # Compute log P( x | Y ) and log P( x_c, x_p1..pN | Y ) and store them for later
        self.node_parents_smoothed = self.msg.parentChildSmoothed( self.U, self.V, self.msg.nodes )
        self.node_smoothed = self.msg.nodeSmoothed( self.U, self.V, self.msg.nodes, self.node_parents_smoothed )

        # Sample x using the gumbel reparametrization trick
        self.graph_relaxed_state = RelaxedStateSampler( self.msg, self.node_parents_smoothed )
        self.msg.forwardPass( self.graph_relaxed_state )

        # Compute the kl divergence from p to q
        # Because we're using q( pi ) := p( pi ) and q( pi0 ) := p( pi0 ), don't need to calculate those terms
        neg_log_z = klpq = self.msg.marginalProb( self.U, self.V )
        for group in self.params.emission_dists.keys():
            klpq -= self.params.emission_dists[ group ].KLPQ( q_params=generative_hyper_params[ group ] )

        # Sample a parameter from the generative network
        generative_params = {}
        for group, hypers in generative_hyper_params.items():
            generative_params[ group ] = self.params.emission_dists[ group ].sampleGenerativeParams( generative_hyper_params=hypers )

        # Compute the likelihood of this sample
        log_likelihood = 0.0
        for node, logit in self.graph_relaxed_state.node_states.items():
            group = self.msg.node_groups[ node ]
            log_likelihood += self.params.emission_dists[ group ].log_likelihood( x=logit, y=self.msg.ys[ node ], cond=self.msg.conds[ node ], generative_params=generative_params[ group ] )

        # Compute the total SVAE loss
        svae_loss = self.s * ( log_likelihood - klpq )
        print( 'svae_loss', svae_loss, flush=True )

        return -svae_loss

    #####################################################################

    def trainSVAE( self, num_iters=100 ):

        svae_params = ( {}, {} )
        for group, dist in self.params.emission_dists.items():
            svae_params[ 0 ][ group ] = dist.recognizer_params
            svae_params[ 1 ][ group ] = dist.generative_hyper_params

        emission_grads = grad( self.svaeLoss )
        def callback( x, i, g ):
            if( i%25 == 0 ):
                print( 'i', i )

        opt_params = adam( emission_grads, svae_params, num_iters=num_iters, callback=callback )

        # Update the model parameters
        for group in self.params.emission_dists.keys():
            self.params.emission_dists[ group ].recognizer_params = opt_params[ 0 ][ group ]
            self.params.emission_dists[ group ].generative_hyper_params = opt_params[ 1 ][ group ]

        return opt_params

######################################################################

class DESOpt( Optimizer ):
    # Deep evolutionary smoother?

    def __init__( self, msg, parameters, inheritance_pattern ):
        super().__init__( msg, parameters )
        self.inheritance_pattern = inheritance_pattern
        self.s = 1.0
        self.params.setMinibatchRatio( self.s )
        self.initial_prior_mfnp     = copy.deepcopy( self.params.initial_dist.prior.nat_params )
        self.transition_prior_mfnps = [ copy.deepcopy( dist.prior.nat_params ) for dist in self.params.transition_dists ]

    #####################################################################

    @primitive
    def marginalLoss( self, recognizer_params, n_iter ):

        # Sample natural parameter
        initial_params = self.params.initial_dist.paramSample( prior_nat_params=self.initial_prior_mfnp )[ 0 ]
        transition_params = [ dist.paramSample( prior_nat_params=mfnp )[ 0 ] for dist, mfnp in zip( self.params.transition_dists, self.transition_prior_mfnps ) ]

        # Run the smoother
        recognizer = partial( self.params.emission_dist.recognize, recognizer_params=recognizer_params, inheritance_pattern=self.inheritance_pattern )
        self.msg.updateParams( initial_params, transition_params, recognizer )
        self.runFilter()

        marginal = self.msg.marginalProb( self.U, self.V )

        return marginal

    #####################################################################

    def marginalLossGrad( self, recognizer_params, n_iter, run_smoother=False, return_flat=False ):

        # Might want to just use the U and V computed from the last smoother run
        if( run_smoother ):

            # Sample natural parameter
            initial_params = self.params.initial_dist.paramSample( prior_nat_params=self.initial_prior_mfnp )[ 0 ]
            transition_params = [ dist.paramSample( prior_nat_params=mfnp )[ 0 ] for dist, mfnp in zip( self.params.transition_dists, self.transition_prior_mfnps ) ]

            # Run the smoother
            recognizer = partial( self.params.emission_dist.recognize, recognizer_params=recognizer_params, inheritance_pattern=self.inheritance_pattern )
            self.msg.updateParams( initial_params, transition_params, recognizer )
            self.runFilter()

        d_logz_d_L = self.msg.emissionPotentialGradients( self.U, self.V, self.msg.nodes )

        _full_g, unflatten = flatten( recognizer_params )
        _full_g = np.zeros_like( _full_g )

        for node, gLogZ in d_logz_d_L:
            y = self.msg.ys[ node ]
            cond = self.msg.conds[ node ]

            def loss0( r, i ):
                return self.params.emission_dist.recognize( y, cond, recognizer_params=r, inheritance_pattern=self.inheritance_pattern )[ 0 ]
            def loss1( r, i ):
                return self.params.emission_dist.recognize( y, cond, recognizer_params=r, inheritance_pattern=self.inheritance_pattern )[ 1 ]
            def loss2( r, i ):
                return self.params.emission_dist.recognize( y, cond, recognizer_params=r, inheritance_pattern=self.inheritance_pattern )[ 2 ]

            g0 = unrolledGrad( grad( loss0 ), recognizer_params )
            g1 = unrolledGrad( grad( loss1 ), recognizer_params )
            g2 = unrolledGrad( grad( loss2 ), recognizer_params )

            _g0, _ = flatten( g0 )
            _g1, _ = flatten( g1 )
            _g2, _ = flatten( g2 )

            _g = _g0*gLogZ[ 0 ] + _g1*gLogZ[ 1 ] + _g2*gLogZ[ 2 ]

            _full_g += _g

        if( return_flat ):
            full_g = _full_g, unflatten
        else:
            full_g = unflatten( _full_g )

        return full_g

    #####################################################################

    def trainMarginal( self, num_iters=100 ):

        params = self.params.emission_dist.recognizer_params

        emission_grads = grad( self.svaeLoss )
        def callback( x, i, g ):
            if( i%25 == 0 ):
                print( 'i', i )

        gs = emission_grads( params )

        opt_params = adam( emission_grads, params, num_iters=num_iters, callback=callback )

        # Update the model parameters
        self.params.emission_dist.recognizer_params = opt_params

        return opt_params


######################################################################

class GroupDESOpt( Optimizer ):

    def __init__( self, msg, parameters, inheritance_pattern ):
        super().__init__( msg, parameters )
        self.inheritance_pattern = inheritance_pattern
        self.s = 1.0
        self.params.setMinibatchRatio( self.s )
        self.initial_prior_mfnp    = dict( [ ( group, copy.deepcopy( dist.prior.nat_params ) ) for group, dist in self.params.initial_dists.items() ] )
        self.transition_prior_mfnps = dict( [ ( group, dict( [ ( shape, copy.deepcopy( dist.prior.nat_params ) ) for shape, dist in dists.items() ] ) ) for group, dists in self.params.transition_dists.items() ] )

    #####################################################################

    def marginalLoss( self, recognizer_params, n_iter ):

        # Sample natural parameter
        initial_params    = dict( [ ( group, dist.paramSample( prior_nat_params=self.initial_prior_mfnp[ group ] )[ 0 ] ) for group, dist in self.params.initial_dists.items() ] )
        transition_params = dict( [ ( group, [ dist.paramSample( prior_nat_params=self.transition_prior_mfnps[ group ][ shape ] )[ 0 ] for shape, dist in dists.items() ] ) for group, dists in self.params.transition_dists.items() ] )

        # Run the smoother
        recognizers = {}
        for group, dist in self.params.emission_dists.items():
            recognizers[ group ] = partial( dist.recognize, recognizer_params=recognizer_params[ group ], inheritance_pattern=self.inheritance_pattern )

        self.msg.updateParams( initial_params, transition_params, recognizers )
        self.runFilter()

        marginal = self.msg.marginalProb( self.U, self.V )

        return marginal

    #####################################################################

    def marginalLossGrad( self, recognizer_params, n_iter, run_smoother=False, return_flat=False ):

        # Might want to just use the U and V computed from the last smoother run
        if( run_smoother ):

            # Sample natural parameter
            initial_params    = dict( [ ( group, dist.paramSample( prior_nat_params=self.initial_prior_mfnp[ group ] )[ 0 ] ) for group, dist in self.params.initial_dists.items() ] )
            transition_params = dict( [ ( group, [ dist.paramSample( prior_nat_params=self.transition_prior_mfnps[ group ][ shape ] )[ 0 ] for shape, dist in dists.items() ] ) for group, dists in self.params.transition_dists.items() ] )

            # Run the smoother
            recognizers = {}
            for group, dist in self.params.emission_dists.items():
                recognizers[ group ] = partial( dist.recognize, recognizer_params=recognizer_params[ group ], inheritance_pattern=self.inheritance_pattern )

            self.msg.updateParams( initial_params, transition_params, recognizers )
            self.runFilter()

        d_logz_d_L = self.msg.emissionPotentialGradients( self.U, self.V, self.msg.nodes )

        unflatten, _full_g = {}, {}
        for group in self.params.emission_dists.keys():
            _full_g[ group ], unflatten[ group ] = flatten( recognizer_params[ group ] )
            _full_g[ group ] = np.zeros_like( _full_g[ group ] )

        for node, gLogZ in d_logz_d_L:

            group = self.msg.node_groups[ node ]
            y = self.msg.ys[ node ]
            cond = self.msg.conds[ node ]
            sex = cond[ 0 ]

            if( sex == 'female' ):
                def loss0( r, i ):
                    return self.params.emission_dists[ group ].recognize( y, cond, recognizer_params=r, inheritance_pattern=self.inheritance_pattern )[ 0 ]
                def loss1( r, i ):
                    return self.params.emission_dists[ group ].recognize( y, cond, recognizer_params=r, inheritance_pattern=self.inheritance_pattern )[ 1 ]
                def loss2( r, i ):
                    return self.params.emission_dists[ group ].recognize( y, cond, recognizer_params=r, inheritance_pattern=self.inheritance_pattern )[ 2 ]

                g0 = unrolledGrad( grad( loss0 ), recognizer_params[ group ] )
                g1 = unrolledGrad( grad( loss1 ), recognizer_params[ group ] )
                g2 = unrolledGrad( grad( loss2 ), recognizer_params[ group ] )

                _g0, _ = flatten( g0 )
                _g1, _ = flatten( g1 )
                _g2, _ = flatten( g2 )

                _g = _g0*gLogZ[ 0 ] + _g1*gLogZ[ 1 ] + _g2*gLogZ[ 2 ]
            elif( sex == 'male' ):
                def loss0( r, i ):
                    return self.params.emission_dists[ group ].recognize( y, cond, recognizer_params=r, inheritance_pattern=self.inheritance_pattern )[ 0 ]
                def loss1( r, i ):
                    return self.params.emission_dists[ group ].recognize( y, cond, recognizer_params=r, inheritance_pattern=self.inheritance_pattern )[ 1 ]

                g0 = unrolledGrad( grad( loss0 ), recognizer_params[ group ] )
                g1 = unrolledGrad( grad( loss1 ), recognizer_params[ group ] )

                _g0, _ = flatten( g0 )
                _g1, _ = flatten( g1 )

                _g = _g0*gLogZ[ 0 ] + _g1*gLogZ[ 1 ]
            else:
                def loss0( r, i ):
                    return self.params.emission_dists[ group ].recognize( y, cond, recognizer_params=r, inheritance_pattern=self.inheritance_pattern )[ 0 ]
                def loss1( r, i ):
                    return self.params.emission_dists[ group ].recognize( y, cond, recognizer_params=r, inheritance_pattern=self.inheritance_pattern )[ 1 ]
                def loss2( r, i ):
                    return self.params.emission_dists[ group ].recognize( y, cond, recognizer_params=r, inheritance_pattern=self.inheritance_pattern )[ 2 ]
                def loss3( r, i ):
                    return self.params.emission_dists[ group ].recognize( y, cond, recognizer_params=r, inheritance_pattern=self.inheritance_pattern )[ 3 ]
                def loss4( r, i ):
                    return self.params.emission_dists[ group ].recognize( y, cond, recognizer_params=r, inheritance_pattern=self.inheritance_pattern )[ 4 ]

                g0 = unrolledGrad( grad( loss0 ), recognizer_params[ group ] )
                g1 = unrolledGrad( grad( loss1 ), recognizer_params[ group ] )
                g2 = unrolledGrad( grad( loss2 ), recognizer_params[ group ] )
                g3 = unrolledGrad( grad( loss3 ), recognizer_params[ group ] )
                g4 = unrolledGrad( grad( loss4 ), recognizer_params[ group ] )

                _g0, _ = flatten( g0 )
                _g1, _ = flatten( g1 )
                _g2, _ = flatten( g2 )
                _g3, _ = flatten( g3 )
                _g4, _ = flatten( g4 )

                _g = _g0*gLogZ[ 0 ] + _g1*gLogZ[ 1 ] + _g2*gLogZ[ 2 ] + _g3*gLogZ[ 3 ] + _g4*gLogZ[ 4 ]

            _full_g[ group ] += _g

        full_g = {}
        for group in self.params.emission_dists.keys():
            full_g[ group ] = unflatten[ group ]( _full_g[ group ] )

        if( return_flat ):
            return flatten( full_g )

        return full_g

    #####################################################################

    def trainMarginal( self, num_iters=100 ):

        params = {}
        for group, dist in self.params.emission_dists.items():
            params[ group ] = dist.recognizer_params

        emission_grads = grad( self.marginalLoss )
        def callback( x, i, g ):
            if( i%25 == 0 ):
                print( 'i', i )

        gs = emission_grads( params )

        opt_params = adam( emission_grads, params, num_iters=num_iters, callback=callback )

        # Update the model parameters
        for group in self.params.emission_dists.keys():
            self.params.emission_dists[ group ].recognizer_params = opt_params[ group ]

        return opt_params
