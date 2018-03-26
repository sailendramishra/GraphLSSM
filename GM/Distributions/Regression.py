import numpy as np
from Base import ExponentialFam
from Normal import Normal
from MatrixNormalInverseWishart import MatrixNormalInverseWishart


class Regression( ExponentialFam ):

    priorClass = MatrixNormalInverseWishart

    def __init__( self, A=None, sigma=None, prior=None, hypers=None ):
        super( Regression, self ).__init__( A, sigma, prior=prior, hypers=hypers )

    ##########################################################################

    @property
    def A( self ):
        return self._params[ 0 ]

    @property
    def sigma( self ):
        return self._params[ 1 ]

    ##########################################################################

    @classmethod
    def dataN( cls, x ):
        xs, ys = x
        return ys.shape[ 0 ]

    ##########################################################################

    @classmethod
    def standardToNat( cls, A, sigma ):

        sigInv = np.linalg.inv( sigma )

        n1 = -0.5 * sigInv
        n2 = -0.5 * A.T @ sigInv @ A
        n3 = A.T @ sigInv

        return n1, n2, n3

    @classmethod
    def natToStandard( cls, n1, n2, n3 ):
        sigma = -0.5 * np.linalg.inv( n1 )
        A = sigma @ n3.T
        return A, sigma

    ##########################################################################

    @property
    def constParams( self ):
        return None

    ##########################################################################

    @classmethod
    def sufficientStats( cls, x, constParams=None, forPost=False ):
        # Compute T( x )

        cls.dataN( x )

        x, y = x

        if( x.ndim == 1 ):
            x = x.reshape( ( 1, -1 ) )
        if( y.ndim == 1 ):
            y = y.reshape( ( 1, -1 ) )

        t1 = y.T.dot( y )
        t2 = x.T.dot( x )
        t3 = x.T.dot( y )

        if( forPost ):
            t4 = x.shape[ 0 ]
            t5 = x.shape[ 0 ]
            return t1, t2, t3, t4, t5
        return t1, t2, t3

    @classmethod
    def log_partition( cls, x=None, params=None, natParams=None, split=False ):
        # Compute A( Ѳ ) - log( h( x ) )
        assert ( params is None ) ^ ( natParams is None )
        A, sigma = params if params is not None else cls.natToStandard( *natParams )

        p = sigma.shape[ 0 ]

        A1 = 0.5 * np.linalg.slogdet( sigma )[ 1 ]
        A2 = p / 2 * np.log( 2 * np.pi )

        if( split ):
            return A1, A2
        return A1 + A2

    ##########################################################################

    @classmethod
    def sample( cls, x=None, params=None, natParams=None, size=1 ):
        # Sample from P( x | Ѳ; α )
        assert ( params is None ) ^ ( natParams is None )
        A, sigma = params if params is not None else cls.natToStandard( *natParams )
        D = A.shape[ 1 ]
        if( x is None ):
            x = np.array( [ Normal.sample( params=( np.zeros( D ), np.eye( D ) ), size=1 ) for _ in range( size ) ] )
            y = np.array( [ Normal.sample( params=( A.dot( _x ), sigma ), size=1 ) for _x in x ] )
            return ( x, y )
        return Normal.sample( params=( A.dot( x ), sigma ), size=size )

    ##########################################################################

    @classmethod
    def log_likelihood( cls, x, params=None, natParams=None ):
        # Compute P( x | Ѳ; α )
        assert ( params is None ) ^ ( natParams is None )
        A, sigma = params if params is not None else cls.natToStandard( *natParams )

        x, y = x
        assert x.shape == y.shape
        if( x.ndim != 1 ):
            return sum( [ Normal.log_likelihood( _y, ( A.dot( _x ), sigma ) ) for _x, _y in zip( x, y ) ] )
        return Normal.log_likelihood( y, ( A.dot( x ), sigma ) )
