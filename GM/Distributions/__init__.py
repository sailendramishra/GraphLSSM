import sys
sys.path.append( '/Users/Eddie/GenModels/GM/Distributions/' )

from .Base import Distribution, Conjugate, ExponentialFam, TensorExponentialFam
from .MatrixNormalInverseWishart import MatrixNormalInverseWishart
from .Regression import Regression
from .InverseWishart import InverseWishart
from .NormalInverseWishart import NormalInverseWishart
from .Normal import Normal
from .Dirichlet import Dirichlet
from .Categorical import Categorical
from .TensorNormal import TensorNormal
from .TensorRegression import TensorRegression
from .TensorCategorical import TensorCategorical