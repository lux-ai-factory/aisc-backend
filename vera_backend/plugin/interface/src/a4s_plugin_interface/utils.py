class classproperty:
    """
    Descriptor for creating a class-level property.

    Allows the definition of a property on a class, similar to @property for instances,
    so the property can be accessed as `ClassName.attribute` rather than `instance.attribute`.

    Example:
        class ExampleClass:
            _value = 123

            @classproperty
            def value(cls):
                return cls._value

        print(ExampleClass.value)  # Output: 123

    Parameters:
        func (function): A callable that accepts the class as an argument and returns a value.
    """

    def __init__(self, func):
        """
        Initializes the descriptor with the getter function.

        Parameters:
            func (function): Function taking the class as an argument.
        """
        self.func = func

    def __get__(self, instance, owner):
        """
        Retrieves the class-level property.

        Parameters:
            instance (object): The instance accessing the attribute
                               (None when accessed via the class).
            owner (type): The class on which the property was accessed.

        Returns:
            Any: The result of invoking the getter function with the class as an argument.
        """
        return self.func(owner)
