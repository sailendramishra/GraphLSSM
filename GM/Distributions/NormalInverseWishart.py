import numpy as np
from GenModels.GM.Distributions.Base import ExponentialFam
from scipy.special import multigammaln
from GenModels.GM.Distributions.InverseWishart import InverseWishart
from GenModels.GM.Distributions.Normal import Normal
from GenModels.GM.Utility import *

class NormalInverseWishart( ExponentialFam ):
    # This class is written with the intention of making it a prior for
    # a normal distribution with an unknown mean and covariance

    def __init__( self, mu_0, kappa, psi, nu, Q=0, prior=None, hypers=None ):
        super( NormalInverseWishart, self ).__init__( mu_0, kappa, psi, nu, Q, prior=prior, hypers=hypers )

    ##########################################################################

    @property
    def mu_0( self ):
        return self._params[ 0 ]

    @property
    def kappa( self ):
        return self._params[ 1 ]

    @property
    def psi( self ):
        return self._params[ 2 ]

    @property
    def nu( self ):
        return self._params[ 3 ]

    ##########################################################################

    @classmethod
    def paramShapes( cls, D=None ):
        assert D is not None
        return [ ( D, ), D, ( D, D ), D, 0 ]

    @classmethod
    def inferDims( cls, params=None ):
        assert params is not None
        mu_0, kappa, psi, nu, Q = params
        return { 'D': mu_0.shape[ 0 ] }

    @classmethod
    def outputShapes( cls, D=None ):
        assert D is not None
        return [ ( D, ), ( D, D ) ]

    ##########################################################################

    @classmethod
    def standardToNat( cls, mu_0, kappa, psi, nu, Q ):
        n1 = kappa * np.outer( mu_0, mu_0 ) + psi
        n2 = kappa * mu_0
        n3 = kappa
        n4 = nu + psi.shape[ 0 ] + 2
        n5 = 1 + Q
        return n1, n2, n3, n4, n5

    @classmethod
    def natToStandard( cls, n1, n2, n3, n4, n5 ):
        kappa = n3
        mu_0 = 1 / kappa * n2
        psi = n1 - kappa * np.outer( mu_0, mu_0 )
        p = mu_0.shape[ 0 ]
        nu = n4 - p - 2

        # The roll of Q is to offset excess normal base measures!
        Q = n5 - 1
        return mu_0, kappa, psi, nu, Q

    ##########################################################################

    @property
    def constParams( self ):
        return None

    ##########################################################################

    @classmethod
    def sufficientStats( cls, x, constParams=None, forPost=False ):
        # Compute T( x )
        if( cls.dataN( x ) > 1 ):
            t = ( 0, 0, 0, 0, 0 )
            for _x in x:
                t = np.add( t, cls.sufficientStats( _x, forPost=forPost ) )
            return t

        t1, t2 = Normal.standardToNat( *x )
        t3, t4, t5 = Normal.log_partition( params=x, split=True )
        return t1, t2, -t3, -t4, -t5

    @classmethod
    @checkExpFamArgs
    def log_partition( cls, x, params=None, natParams=None, split=False ):
        # Compute A( Ѳ ) - log( h( x ) )
        mu_0, kappa, psi, nu, Q = params if params is not None else cls.natToStandard( *natParams )

        p = psi.shape[ 0 ]

        A1, A2, A3 = InverseWishart.log_partition( x, params=( psi, nu ), split=True )
        A4 = -p / 2 * np.log( kappa )
        A5 = -Q * ( p / 2 * np.log( 2 * np.pi ) )

        return A1, A2, A3, A4, A5

    ##########################################################################

    @classmethod
    @fullSampleSupport
    @checkExpFamArgs( allowNone=True )
    def sample( cls, params=None, natParams=None ):
        # Sample from P( x | Ѳ; α )

        mu_0, kappa, psi, nu, _ = params if params is not None else cls.natToStandard( *natParams )

        sigma = InverseWishart.sample( params=( psi, nu ) )
        mu = Normal.sample( params=( mu_0, sigma / kappa ) )
        return mu, sigma

    ##########################################################################

    @classmethod
    @fullLikelihoodSupport
    @checkExpFamArgs
    def log_likelihood( cls, x, params=None, natParams=None ):
        # Compute P( x | Ѳ; α )
        mu_0, kappa, psi, nu, _ = params if params is not None else cls.natToStandard( *natParams )

        mu, sigma = x
        return InverseWishart.log_likelihood( sigma, params=( psi, nu ) ) + \
               Normal.log_likelihood( mu, params=( mu_0, sigma / kappa ) )
