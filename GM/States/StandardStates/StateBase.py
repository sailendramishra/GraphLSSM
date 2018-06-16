import numpy as np
from GenModels.GM.Distributions import ExponentialFam
from abc import ABC, abstractmethod

class StateBase( ExponentialFam ):

    # This is a distribution over P( x, y | ϴ ).
    # Will still be able to do inference over P( x | y, ϴ )

    def __init__( self, *args, **kwargs ):
        self._normalizerValid = False
        super( StateBase, self ).__init__( *args, **kwargs )

    ######################################################################

    @property
    def params( self ):
        if( self.naturalChanged ):
            self._params = self.natToStandard( *self.natParams )
            self.naturalChanged = False
        return self._params

    @property
    def natParams( self ):
        if( self.standardChanged ):
            self._natParams = self.standardToNat( *self.params )
            self.standardChanged = False
        return self._natParams

    @params.setter
    def params( self, val ):
        self.standardChanged = True
        self.naturalChanged = False
        self.updateParams( *val )
        self._params = val

    @natParams.setter
    def natParams( self, val ):
        self.naturalChanged = True
        self.standardChanged = False
        self.updateNatParams( *val )
        self._natParams = val

    ##########################################################################
    ## Mean field parameters for variational inference.  Only update from ##
    ## natrual mean field params ##

    @property
    def mfParams( self ):
        if( self.mfNaturalChanged ):
            self._mfParams = self.natToStandard( *self.mfNatParams )
            self.mfNaturalChanged = False
        return self._mfParams

    @mfParams.setter
    def mfParams( self, val ):
        assert 0, 'Don\'t update this way!  All of the message passing algorithms (should) only work with natural params!'

    @property
    def mfNatParams( self ):
        return self._mfNatParams

    @mfNatParams.setter
    def mfNatParams( self, val ):
        self.mfNaturalChanged = True
        self.updateNatParams( *val )
        self._mfNatParams = val

    ######################################################################

    @property
    def lastNormalizer( self ):
        if( self._normalizerValid ):
            return self._lastNormalizer
        return None

    @lastNormalizer.setter
    def lastNormalizer( self, val ):
        self._normalizerValid = True
        self._lastNormalizer = val

    ######################################################################

    @property
    @abstractmethod
    def T( self ):
        pass

    @abstractmethod
    def forwardFilter( self ):
        pass

    @abstractmethod
    def backwardFilter( self ):
        pass

    @abstractmethod
    def preprocessData( self, ys ):
        pass

    @abstractmethod
    def parameterCheck( self, *args ):
        pass

    @abstractmethod
    def updateParams( self, *args ):
        pass

    @abstractmethod
    def genStates( self ):
        pass

    @abstractmethod
    def sampleStep( self, *args ):
        pass

    @abstractmethod
    def likelihoodStep( self, *args ):
        pass

    @abstractmethod
    def forwardArgs( self, t, beta, prevX ):
        pass

    @abstractmethod
    def backwardArgs( self, t, alpha, prevX ):
        pass

    @abstractmethod
    def sequenceLength( cls, x ):
        pass

    @abstractmethod
    def nMeasurements( cls, x ):
        pass

    ######################################################################

    def noFilterForwardRecurse( self, workFunc ):
        lastVal = None
        for t in range( self.T ):
            args = self.forwardArgs( t, None, lastVal )
            lastVal = workFunc( t, *args )

        self._normalizerValid = False

    def forwardFilterBackwardRecurse( self, workFunc, **kwargs ):
        # P( x_1:T | y_1:T ) = prod_{ x_t=T:1 }[ P( x_t | x_t+1, y_1:t ) ] * P( x_T | y_1:T )
        alphas = self.forwardFilter( **kwargs )

        lastVal = None
        for t in reversed( range( self.T ) ):
            args = self.backwardArgs( t, alphas[ t ], lastVal )
            lastVal = workFunc( t, *args )

        # Reset the normalizer flag
        self._normalizerValid = False

    def backwardFilterForwardRecurse( self, workFunc, **kwargs ):
        # P( x_1:T | y_1:T ) = prod_{ x_t=1:T }[ P( x_t+1 | x_t, y_t+1:T ) ] * P( x_1 | y_1:T )
        betas = self.backwardFilter( **kwargs )

        lastVal = None
        for t in range( self.T ):
            args = self.forwardArgs( t, betas[ t ], lastVal )
            lastVal = workFunc( t, *args )

        # Reset the normalizer flag
        self._normalizerValid = False

    ######################################################################

    @abstractmethod
    def sampleEmissions( self, x ):
        # Sample from P( y | x, ϴ )
        pass

    @abstractmethod
    def emissionLikelihood( self, x, ys ):
        # Compute P( y | x, ϴ )
        pass

    ######################################################################

    @abstractmethod
    def conditionedExpectedSufficientStats( self, alphas, betas ):
        pass

    @classmethod
    def expectedSufficientStats( cls, ys=None, params=None, natParams=None, returnNormalizer=False, **kwargs ):
        assert ( params is None ) ^ ( natParams is None )
        params = params if params is not None else cls.natToStandard( *natParams )
        dummy = cls( *params, paramCheck=False )
        return dummy.iexpectedSufficientStats( ys=ys, returnNormalizer=returnNormalizer, **kwargs )

    def iexpectedSufficientStats( self, ys=None, preprocessKwargs={}, filterKwargs={}, returnNormalizer=False ):

        if( ys is None ):
            return super( StateBase, self ).iexpectedSufficientStats()

        alphas, betas = self.EStep( ys=ys, preprocessKwargs=preprocessKwargs, filterKwargs=filterKwargs )
        stats = self.conditionedExpectedSufficientStats( ys, alphas, betas )

        if( returnNormalizer ):
            return stats, self.lastNormalizer
        return stats

    ######################################################################

    @classmethod
    def sample( cls, ys=None, params=None, natParams=None, measurements=1, T=None, forwardFilter=True, size=1, **kwargs ):
        assert ( params is None ) ^ ( natParams is None )
        params = params if params is not None else cls.natToStandard( *natParams )
        dummy = cls( *params )
        return dummy.isample( ys=ys, measurements=measurements, T=T, forwardFilter=forwardFilter, size=size, **kwargs )

    ######################################################################

    def conditionedSample( self, ys=None, forwardFilter=True, preprocessKwargs={}, filterKwargs={} ):
        # Sample x given y

        size = self.dataN( ys, conditionOnY=True, checkY=True )

        if( size > 1 ):
            it = iter( ys )
        else:
            it = iter( ys )
            # it = iter( [ ys ] )

        ans = []
        for y in it:

            self.preprocessData( ys=y, **preprocessKwargs )

            x = self.genStates()

            def workFunc( t, *args ):
                nonlocal x
                x[ t ] = self.sampleStep( *args )
                return x[ t ]

            if( forwardFilter ):
                self.forwardFilterBackwardRecurse( workFunc, **filterKwargs )
            else:
                self.backwardFilterForwardRecurse( workFunc, **filterKwargs )

            ans.append( ( x, y ) )

        ans = tuple( list( zip( *ans ) ) )

        self.checkShape( ans )
        return ans

    ######################################################################

    def fullSample( self, measurements=2, T=None, size=1 ):
        # Sample x and y

        assert T is not None
        self.T = T

        ans = []

        for _ in range( size ):
            x = self.genStates()

            def workFunc( t, *args ):
                nonlocal x
                x[ t ] = self.sampleStep( *args )
                return x[ t ]

            # This is if we want to sample from P( x, y | ϴ )
            self.noFilterForwardRecurse( workFunc )

            # We can have multiple measurements from the same latent state
            ys = np.array( [ self.sampleEmissions( x ) for _ in range( measurements ) ] )
            ys = np.swapaxes( ys, 0, 1 )

            ans.append( ( x, ys[ 0 ] ) )

        ans = tuple( list( zip( *ans ) ) )

        self.checkShape( ans )
        return ans

    ######################################################################

    def isample( self, ys=None, measurements=1, T=None, forwardFilter=True, size=1 ):
        # Probably override this for each child class
        if( ys is not None ):
            return self.conditionedSample( ys=ys, forwardFilter=forwardFilter )
        return self.fullSample( measurements=measurements, T=T, size=size )

    ######################################################################

    @classmethod
    def log_likelihood( cls, x, params=None, natParams=None, forwardFilter=True, conditionOnY=False, seperateLikelihoods=False, preprocessKwargs={}, filterKwargs={} ):
        assert ( params is None ) ^ ( natParams is None )
        params = params if params is not None else cls.natToStandard( *natParams )

        dummy = cls( *params )
        return dummy.ilog_likelihood( x, forwardFilter=forwardFilter, conditionOnY=conditionOnY, seperateLikelihoods=seperateLikelihoods, preprocessKwargs=preprocessKwargs, filterKwargs=filterKwargs )

    def ilog_likelihood( self, x, forwardFilter=True, conditionOnY=False, expFam=False, preprocessKwargs={}, filterKwargs={}, seperateLikelihoods=False ):

        if( expFam ):
            return self.log_likelihoodExpFam( x, constParams=self.constParams, natParams=self.natParams )

        size = self.dataN( x )

        x, ys = x

        if( size > 1 ):
            it = zip( x, ys )
        else:
            # Need to add for case where size is 1 and unpacked vs size is 1 and packed
            it = zip( x, ys )
            # it = iter( [ [ x, ys ] ] )

        ans = np.zeros( size )

        for i, ( x, ys ) in enumerate( it ):

            self.preprocessData( ys=ys, **preprocessKwargs )

            def workFunc( t, *args ):
                nonlocal ans, x
                term = self.likelihoodStep( x[ t ], *args )
                ans[ i ] += term
                return x[ t ]

            if( conditionOnY == False ):
                # This is if we want to compute P( x, y | ϴ )
                self.noFilterForwardRecurse( workFunc )
                ans[ i ] += self.emissionLikelihood( x, ys )
            else:
                if( forwardFilter ):
                    # Otherwise compute P( x | y, ϴ )
                    assert conditionOnY == True
                    self.forwardFilterBackwardRecurse( workFunc, **filterKwargs )
                else:
                    assert conditionOnY == True
                    self.backwardFilterForwardRecurse( workFunc, **filterKwargs )

        if( seperateLikelihoods == True ):
            return ans

        return ans.sum()

    ######################################################################

    @classmethod
    def log_marginal( cls, x, params=None, natParams=None, seperateMarginals=False, preprocessKwargs={}, filterKwargs={}, alphas=None, betas=None ):
        assert ( params is None ) ^ ( natParams is None )
        params = params if params is not None else cls.natToStandard( *natParams )

        dummy = cls( *params )
        return dummy.ilog_marginal( x, seperateMarginals=seperateMarginals, preprocessKwargs=preprocessKwargs, filterKwargs=filterKwargs, alphas=alphas, betas=betas )

    def ilog_marginal( self, ys, seperateMarginals=False, preprocessKwargs={}, filterKwargs={}, alphas=None, betas=None ):

        size = self.dataN( ys, conditionOnY=True, checkY=True )

        def work( _ys ):
            self.preprocessData( ys=_ys, **preprocessKwargs )
            alpha = self.forwardFilter( **filterKwargs )
            beta = self.backwardFilter( **filterKwargs )
            return self.log_marginalFromAlphaBeta( alpha[ 0 ], beta[ 0 ] )

        # if( size == 1 ):
        #     return work( ys )

        ans = np.empty( size )

        if( alphas is not None or betas is not None ):
            assert alphas is not None and betas is not None
            for i, ( _ys, _alpha, _beta ) in enumerate( zip( ys, alphas, betas ) ):
                ans[ i ] = self.log_marginalFromAlphaBeta( _alpha[ 0 ], _beta[ 0 ] )
        else:
            for i, _ys in enumerate( ys ):
                ans[ i ] = work( _ys )

        if( seperateMarginals == False ):
            ans = ans.sum()

        return ans

    ######################################################################

    def EStep( self, ys=None, preprocessKwargs={}, filterKwargs={} ):

        def work( _ys ):
            self.preprocessData( ys=_ys, **preprocessKwargs )
            a = self.forwardFilter( **filterKwargs )
            b = self.backwardFilter( **filterKwargs )

            return a, b

        if( self.dataN( ys, conditionOnY=True, checkY=True ) > 1 ):
            alphas, betas = zip( *[ work( _ys ) for _ys in ys ] )
        else:
            alphas, betas = zip( *[ work( _ys ) for _ys in ys ] )
            # alphas, betas = work( ys )

        self.lastNormalizer = self.ilog_marginal( ys, alphas=alphas, betas=betas )
        return alphas, betas

    @abstractmethod
    def MStep( self, ys, alphas, betas ):
        pass

    ######################################################################

    @classmethod
    def ELBO( cls, ys=None,
                   mfParams=None,
                   mfNatParams=None,
                   priorMFParams=None,
                   priorMFNatParams=None,
                   priorParams=None,
                   priorNatParams=None,
                   normalizer=None,
                   **kwargs ):

        if( ys is None ):
            assert normalizer is not None
        else:
            mfParams = mfParams if mfParams is not None else cls.natToStandard( *mfNatParams )
            dummy = cls( *mfParams, paramCheck=False )
            dummy.EStep( ys=ys, **kwargs )
            normalizer = dummy.lastNormalizer

        klDiv = cls.priorClass.KLDivergence( params1=priorParams, natParams1=priorNatParams, params2=priorMFParams, natParams2=priorMFNatParams )

        return normalizer + klDiv

    def iELBO( self, ys, **kwargs ):

        # E_{ q( x, Ѳ ) }[ log_P( y, x | Ѳ ) - log_q( x ) ] = normalization term after message passing
        # E_{ q( x, Ѳ ) }[ log_p( Ѳ ) - log_q( Ѳ ) ] = KL divergence between p( Ѳ ) and q( Ѳ )


        # Probably want a better way to do this than just creating a dummy state instance
        dummy = type( self )( *self.mfParams, paramCheck=False )
        dummy.EStep( ys=ys, **kwargs )
        normalizer = dummy.lastNormalizer

        klDiv = self.prior.iKLDivergence( otherNatParams=self.prior.mfNatParams )

        return normalizer + klDiv
