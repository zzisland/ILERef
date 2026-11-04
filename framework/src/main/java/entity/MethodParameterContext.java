package entity;

import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class MethodParameterContext {
    /** 参数类型，例如 String、ConsumerRecord<byte[], byte[]>。 */
    private String type;

    /** 参数名，例如 input、record。 */
    private String name;
}
